import math
import random
import uuid
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from treys import Card as TCard, Evaluator

from .engine import Hand


class SeatInput(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")
    name: str = Field(default="玩家",max_length=40)
    stack: int = Field(default=20000,ge=1,le=100000000)


class HistoryEvent(BaseModel):
    id: str = Field(default_factory=lambda:uuid.uuid4().hex[:8])
    kind: Literal["fold","check","call","raise","board"]
    player: str | None = None
    amount: int | None = Field(default=None,ge=1)
    cards: list[str] = Field(default_factory=list)
    time: str | None = None


class AdvisorInput(BaseModel):
    mode: Literal["cash","sng"] = "cash"
    seats: list[SeatInput] = Field(min_length=2,max_length=10)
    button: int = Field(ge=0,le=9)
    hero: str
    hole_cards: list[str] = Field(min_length=2,max_length=2)
    small_blind: int = Field(default=50,ge=1)
    big_blind: int = Field(default=100,ge=1)
    ante: int = Field(default=0,ge=0)
    events: list[HistoryEvent] = Field(default_factory=list,max_length=200)
    samples: int = Field(default=3000,ge=200,le=20000)
    model_entry_id: str = "jev"
    billing_mode: Literal["hosted","personal"] = "hosted"
    stack_mode: Literal["exact","assumed"] = "exact"
    assumed_stack_bb: int = Field(default=100,ge=10,le=1000)

    @model_validator(mode="after")
    def check_input(self):
        if self.stack_mode=="assumed":
            for seat in self.seats:
                seat.stack=self.big_blind*self.assumed_stack_bb
        ids=[s.id for s in self.seats]
        if len(set(ids))!=len(ids) or self.hero not in ids or self.button>=len(ids):
            raise ValueError("座位 ID/庄位/自己的座位无效")
        if self.small_blind>self.big_blind:
            raise ValueError("小盲不可大于大盲")
        cards=self.hole_cards+[c for e in self.events if e.kind=="board" for c in e.cards]
        if len(set(cards))!=len(cards):
            raise ValueError("存在重复牌")
        for card in cards:
            if len(card)!=2 or card[0] not in "23456789TJQKA" or card[1] not in "cdhs":
                raise ValueError(f"牌面格式无效：{card}（例如 As、Td）")
        if len({e.id for e in self.events})!=len(self.events):
            raise ValueError("事件 ID 不可重复")
        return self


def reconstruct(data: AdvisorInput) -> Hand:
    hand=Hand([s.model_dump() for s in data.seats],data.button,
              (data.small_blind,data.big_blind,data.ante),0,mode=data.mode,
              hero=data.hero,hole_cards=data.hole_cards,manual=True)
    for index,event in enumerate(data.events):
        try:
            if event.kind=="board":
                hand.add_board(event.cards)
            else:
                if event.player!=hand.actor:
                    raise ValueError(f"应由 {hand.actor} 行动")
                if event.kind=="raise" and event.amount is None:
                    raise ValueError("请填写本街加注到的总额")
                hand.apply(f"raise_to_{event.amount}" if event.kind=="raise" else event.kind)
        except (ValueError,AssertionError) as exc:
            raise ValueError(f"第 {index+1} 条事件（{event.id}）：{exc}") from None
    hand.stack_assumption=f"未录入实际后手，统一假设开手 {data.assumed_stack_bb}BB；短码/全下/边池建议须改用实际后手" if data.stack_mode=="assumed" else None
    return hand


def equity(hand: Hand, hero: str, samples: int=3000):
    snap=hand.snapshot(hero=hero)
    active=[s["id"] for s in snap["seats"] if not s["folded"] and s["position"]!="OUT"]
    if hero not in active:
        return {"available":False,"reason":"自己已经弃牌"}
    board=[TCard.new(c) for c in snap["board"]]
    hero_cards=[TCard.new(c) for c in hand.original_holes[hero]]
    known=set(board+hero_cards)
    deck=[TCard.new(r+s) for r in "23456789TJQKA" for s in "cdhs" if TCard.new(r+s) not in known]
    opponents=[pid for pid in active if pid!=hero]
    evaluator=Evaluator()
    rng=random.Random(6719)
    wins,ties,total,squared=0,0,0.0,0.0
    pots=[p for p in snap["pots"] if hero in p["eligible"]]
    pot_shares=[0.0]*len(pots)
    for _ in range(samples):
        draw=rng.sample(deck,5-len(board)+len(opponents)*2)
        complete_board=board+draw[:5-len(board)]
        offset=5-len(board)
        scores={hero:evaluator.evaluate(complete_board,hero_cards)}
        for i,pid in enumerate(opponents):
            scores[pid]=evaluator.evaluate(complete_board,draw[offset+2*i:offset+2*i+2])
        best=min(scores.values())
        winners=[p for p,v in scores.items() if v==best]
        share=1/len(winners) if hero in winners else 0
        wins+=int(winners==[hero])
        ties+=int(hero in winners and len(winners)>1)
        total+=share
        squared+=share*share
        for i,pot in enumerate(pots):
            eligible={p:v for p,v in scores.items() if p in pot["eligible"]}
            low=min(eligible.values())
            tied=[p for p,v in eligible.items() if v==low]
            pot_shares[i]+=1/len(tied) if hero in tied else 0
    average=total/samples
    error=1.96*math.sqrt(max(0,squared/samples-average*average)/samples)
    eligible_amount=sum(p["amount"] for p in pots)
    return {"available":True,"win":wins/samples,"tie":ties/samples,"equity":average,
            "ci95":[max(0,average-error),min(1,average+error)],"samples":samples,
            "assumption":"未知对手底牌均匀随机；未来公共牌随机发出；假设所有未弃牌对手摊牌，不建模后续弃牌或下注",
            "method":"Monte Carlo","pot_odds":(snap["to_call"]/(snap["pot"]+snap["to_call"]) if snap["to_call"] else 0) if hand.actor==hero else None,
            "pot_odds_note":"多边池时须分别看参与资格；该比例不是行动 EV",
            "settled_pots":[{**p,"equity":pot_shares[i]/samples} for i,p in enumerate(pots)],
            "collected_eligible_pot":eligible_amount}
