import json
from pathlib import Path
import sqlite3
import time
import uuid
from .player_budget import setup as setup_player_budget, check_reservation, owner


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS deleted_rooms(run_id TEXT PRIMARY KEY, deleted REAL);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, updated REAL, body TEXT);
            CREATE TABLE IF NOT EXISTS series(id TEXT PRIMARY KEY, created REAL, body TEXT);
            CREATE TABLE IF NOT EXISTS hands(run_id TEXT, number INTEGER, body TEXT,
              PRIMARY KEY(run_id,number));
            CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY, body TEXT);
            CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY, run_id TEXT, status TEXT,
              reserved REAL, charged REAL, created REAL, body TEXT);
            CREATE INDEX IF NOT EXISTS calls_by_run ON calls(run_id);
        """)
        self.db.commit()
        setup_player_budget(self.db)

    def save_run(self, run: dict, hand: dict | None = None, *, owner_id: str | None = None):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?)", (run["id"],time.time(),json.dumps(run)))
            if owner_id:
                self.db.execute("INSERT INTO room_owners VALUES(?,?,?)", (run["id"],owner_id,time.time()))
            if hand:
                self.db.execute("INSERT OR REPLACE INTO hands VALUES(?,?,?)",
                                (run["id"],hand["spec"]["number"],json.dumps(hand)))

    def run(self, run_id: str):
        row = self.db.execute("SELECT body FROM runs WHERE id=? AND id NOT IN (SELECT run_id FROM deleted_rooms)", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return json.loads(row[0])

    def runs(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT body FROM runs WHERE id NOT IN (SELECT run_id FROM deleted_rooms) ORDER BY updated DESC")]

    def save_series(self, series: dict, run: dict | None = None):
        # Membership and the new tournament become durable in one transaction.
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO series VALUES(?,?,?)",
                            (series["id"],series["created"],json.dumps(series)))
            if run is not None:
                self.db.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?)",
                                (run["id"],time.time(),json.dumps(run)))

    def series(self, series_id: str):
        row=self.db.execute("SELECT body FROM series WHERE id=?",(series_id,)).fetchone()
        if row is None:
            raise KeyError(series_id)
        return json.loads(row[0])

    def all_series(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT body FROM series ORDER BY created DESC")]

    def hand(self, run_id: str, number: int):
        row = self.db.execute("SELECT body FROM hands WHERE run_id=? AND number=?",(run_id,number)).fetchone()
        if not row:
            raise KeyError("手牌不存在")
        return json.loads(row[0])

    def hands(self, run_id: str):
        return [json.loads(r[0]) for r in self.db.execute("SELECT body FROM hands WHERE run_id=? ORDER BY number",(run_id,))]

    def decision(self, decision_id: str):
        row = self.db.execute("SELECT body FROM decisions WHERE id=?",(decision_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_decision(self, decision_id: str, decision: dict):
        with self.db:
            self.db.execute("INSERT INTO decisions VALUES(?,?)",(decision_id,json.dumps(decision)))

    def spend(self, run_id: str | None = None):
        query = "SELECT COALESCE(SUM(CASE WHEN status='pending' THEN reserved ELSE charged END),0) FROM calls"
        params = ()
        if run_id:
            query += " WHERE run_id=?"
            params = (run_id,)
        else:
            query += " WHERE COALESCE(json_extract(body,'$.self_funded'),0)=0"
        return float(self.db.execute(query,params).fetchone()[0])

    def reserve(self, run_id: str, amount: float, global_budget: float, run_budget: float, provider: str="unknown", self_funded: bool=False):
        # Lock before reading allowance, including across connections/processes.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            check_reservation(self.db,run_id,amount,provider,self_funded)
            if (not self_funded and self.spend()+amount > global_budget) or self.spend(run_id)+amount > run_budget:
                raise ValueError("预算不足，已停止新 API 调用")
            call_id = uuid.uuid4().hex
            self.db.execute("INSERT INTO calls VALUES(?,?,?,?,?,?,?)",
                            (call_id,run_id,"pending",amount,0,time.time(),json.dumps({"provider":provider,"self_funded":self_funded,"billing_user":owner(self.db,run_id)})))
        return call_id

    def finish_call(self, call_id: str, charge: float | None, body: dict):
        billing=json.loads(self.db.execute('SELECT body FROM calls WHERE id=?',(call_id,)).fetchone()[0])
        body={**body,**{k:billing[k] for k in ('provider','self_funded','billing_user') if k in billing}}
        with self.db:
            row=self.db.execute('SELECT body FROM calls WHERE id=?',(call_id,)).fetchone()
            if row:
                body={**body,'provider':json.loads(row[0]).get('provider','unknown')}
            self.db.execute("UPDATE calls SET status=?,charged=COALESCE(?,reserved),body=? WHERE id=?",
                            ("estimated" if charge is None else "reported",charge,json.dumps(body),call_id))

    def calls(self, run_id: str | None = None):
        sql,params = "SELECT * FROM calls",()
        if run_id:
            sql += " WHERE run_id=?"
            params=(run_id,)
        return [dict(row) for row in self.db.execute(sql,params)]

    def decision_attempt_count(self, run_id: str, decision_id: str):
        return self.db.execute("SELECT COUNT(*) FROM calls WHERE run_id=? AND json_extract(body,'$.decision_id')=?",
                               (run_id,decision_id)).fetchone()[0]
