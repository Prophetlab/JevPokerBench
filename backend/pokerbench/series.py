"""Durable, sequential SNG series; cash can run alongside the active tournament."""
import asyncio
from copy import deepcopy
import time
import uuid

from .config import RunConfig


def completed(run):
    return (run["status"]=="complete" and bool(run["champion"])
            and all(s["place"] is not None for s in run["seats"]))


class SeriesRunner:
    def __init__(self, runner):
        self.runner,self.store=runner,runner.store
        self.tasks={}
        for series in self.store.all_series():
            if series["status"] in ("running","pausing"):
                series["status"]="paused"
                self.store.save_series(series)

    def create(self, run_id: str, target: int = 50):
        if not 1<=target<=1000:
            raise ValueError("系列赛场数必须为 1–1000")
        run=self.store.run(run_id)
        if run["config"].get("human_player_id"):
            raise ValueError("自组局不加入自动系列赛")
        if run.get("series_id"):
            existing=self.store.series(run["series_id"])
            if existing["target"]!=target:
                raise ValueError("本场已属于另一赛程")
            return existing
        if run["config"]["mode"]!="sng" or run["status"]=="running":
            raise ValueError("请先暂停 SNG，再加入系列赛")
        series={"id":uuid.uuid4().hex[:12],"created":time.time(),"status":"ready",
                "message":"", "target":target,"run_ids":[run_id],"config":deepcopy(run["config"])}
        run["series_id"],run["series_number"]=series["id"],1
        self.store.save_series(series,run)
        return series

    def start(self, series_id: str):
        series=self.store.series(series_id)
        if series_id in self.tasks and not self.tasks[series_id].done():
            if series["status"]=="pausing":
                raise ValueError("正在暂停，请稍后继续")
            return
        if series["status"]=="complete":
            raise ValueError("系列赛已经完成")
        series["status"],series["message"]="running",""
        self.store.save_series(series)
        self.tasks[series_id]=asyncio.create_task(self.play(series_id))

    def pause(self, series_id: str):
        series=self.store.series(series_id)
        if series["status"]=="running":
            series["status"]="pausing"
            self.store.save_series(series)
            self.runner.pause(series["run_ids"][-1])

    async def play(self, series_id: str):
        try:
            while True:
                series=self.store.series(series_id)
                if series["status"]!="running":
                    break
                run=self.store.run(series["run_ids"][-1])
                if not completed(run):
                    self.runner.start(run["id"])
                    await self.runner.tasks[run["id"]]
                    series=self.store.series(series_id)
                    run=self.store.run(run["id"])
                    if not completed(run):
                        series["message"]=run["message"]
                        break
                if len(series["run_ids"])>=series["target"]:
                    series["status"],series["message"]="complete",""
                    self.store.save_series(series)
                    return
                if series["status"]!="running":
                    break
                cfg=deepcopy(series["config"])
                number=len(series["run_ids"])+1
                cfg["name"]=f'{cfg["name"][:80]} · SNG {number:02d}'
                cfg["seed"]=(cfg["seed"]+1000003*(number-1)) % (2**63)
                run=self.runner.create(RunConfig.model_validate(cfg),persist=False)
                run["series_id"],run["series_number"]=series_id,number
                series["run_ids"].append(run["id"])
                self.store.save_series(series,run)
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            series=self.store.series(series_id)
            series["status"]="paused"
            self.store.save_series(series)
            raise
        except Exception as exc:
            series=self.store.series(series_id)
            series["message"]=str(exc)[:300]
        series["status"]="paused"
        self.store.save_series(series)


def public_series(series, store):
    runs=[store.run(rid) for rid in series["run_ids"]]
    finished=[r for r in runs if completed(r)]
    rows=[]
    for entry in series["config"]["entries"]:
        places=[next(s["place"] for s in r["seats"] if s["id"]==entry["id"]) for r in finished]
        rows.append({"id":entry["id"],"name":entry["name"],"color":entry["color"],
                     "completed":len(places),"mean_place":sum(places)/len(places) if places else None,
                     "wins":places.count(1)})
    rows.sort(key=lambda r:r["mean_place"] if r["mean_place"] is not None else float("inf"))
    previous=None
    for index,row in enumerate(rows):
        if index==0 or row["mean_place"]!=previous:
            rank=index+1
        row["rank"]=rank if finished else None
        previous=row["mean_place"]
    return {**{k:series[k] for k in ("id","created","status","message","target","run_ids")},
            "completed":len(finished),"current_run_id":runs[-1]["id"],
            "current_number":len(runs),"standings":rows}
