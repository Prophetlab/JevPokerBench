"""PokerKit owns legality and settlement; this module owns observation boundaries."""
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
import random

from pokerkit import Automation, Card, Deck, Mode, NoLimitTexasHoldem, HoleCardsShowingOrMucking

AUTOMATIONS = (Automation.ANTE_POSTING, Automation.BLIND_OR_STRADDLE_POSTING,
              Automation.BET_COLLECTION, Automation.RUNOUT_COUNT_SELECTION, Automation.HOLE_CARDS_SHOWING_OR_MUCKING,
              Automation.HAND_KILLING, Automation.CHIPS_PUSHING, Automation.CHIPS_PULLING)
STREETS = ["preflop", "flop", "turn", "river"]


@dataclass(frozen=True)
class Action:
    id: str
    kind: str
    label: str
    pay: int
    to: int
    all_in: bool = False

    def json(self):
        return vars(self).copy()


class Hand:
    def __init__(self, seats: list[dict], button: int, blinds: tuple[int,int,int], seed: int,
                 number: int = 1, mode: str = "cash", hero: str | None = None,
                 hole_cards: list[str] | None = None, manual: bool = False,
                 deck: list[str] | None = None, chip_unit: int = 1):
        self.spec = dict(seats=deepcopy(seats), button=button, blinds=list(blinds), seed=seed,
                         number=number, mode=mode, hero=hero, hole_cards=hole_cards, manual=manual, deck=deck, chip_unit=chip_unit)
        self.chip_unit=chip_unit
        self.seats = deepcopy(seats)
        self.button, self.number, self.mode, self.manual = button, number, mode, manual
        self.sb, self.bb, self.ante = blinds
        active = [i for i,s in enumerate(seats) if s["stack"] > 0]
        if len(active) < 2 or button not in active:
            raise ValueError("开手至少两位有筹码的玩家，庄家必须在场")
        # PokerKit: index 0 is SB for 3+ players, BB heads-up; dealer is last.
        self.order = sorted(active, key=lambda i: (i-button-1) % len(seats))
        self.ids = [seats[i]["id"] for i in self.order]
        self.sb_index, self.bb_index = ((1,0) if len(active)==2 else (0,1))
        stacks = [seats[i]["stack"] for i in self.order]
        antes = [0]*len(active)
        # Explicit big-blind-first convention for short-stack BB ante.
        antes[1 if len(active)==2 else self.bb_index] = min(self.ante, max(0, stacks[self.bb_index]-self.bb))
        self.state = NoLimitTexasHoldem.create_state(
            AUTOMATIONS, False, antes, (self.sb,self.bb), self.bb, stacks, len(active),
            mode=Mode.CASH_GAME if mode=="cash" else Mode.TOURNAMENT,
            divmod=lambda amount,count: ((amount//(count*chip_unit))*chip_unit, amount% (count*chip_unit)))
        if deck:
            cards = [Card.parse(c).__next__() for c in deck]
            if len(set(cards)) != 52 or len(cards) != 52:
                raise ValueError("牌堆必须为不重复的完整 52 张牌")
        else:
            cards = list(Deck.STANDARD)
            random.Random(seed).shuffle(cards)
        self.state.deck_cards = deque(cards)
        if manual:
            if hero not in self.ids or not hole_cards or len(hole_cards) != 2 or len(set(hole_cards)) != 2:
                raise ValueError("请选择自己的座位并输入两张不同底牌")
            while self.state.can_deal_hole():
                idx = self.state.hole_dealee_index
                held = len(self.state.hole_cards[idx])
                self.state.deal_hole(hole_cards[held] if self.ids[idx]==hero else "??")
        else:
            while self.state.can_deal_hole():
                self.state.deal_hole()
        self.original_holes = {pid:[repr(c) for c in self.state.hole_cards[i]] for i,pid in enumerate(self.ids)}
        self.folded: set[str] = set()
        self.committed = {pid:stacks[i]-self.state.stacks[i] for i,pid in enumerate(self.ids)}
        self.actions: list[dict] = []
        self.events: list[dict] = []
        self._capture("deal", "底牌发出，盲注入池")
        self.advance()

    @property
    def actor(self):
        idx = self.state.actor_index
        return None if idx is None else self.ids[idx]

    @property
    def street(self):
        return STREETS[self.state.street_index] if self.state.street_index is not None else "complete"

    def legal(self) -> list[Action]:
        s = self.state
        i = s.actor_index
        if i is None or not s.status:
            return []
        call = s.checking_or_calling_amount or 0
        result = []
        if call and s.can_fold():
            result.append(Action("fold", "fold", "弃牌", 0, s.bets[i]))
        if s.can_check_or_call():
            result.append(Action("call" if call else "check", "call" if call else "check",
                                 "跟注全下" if call and call==s.stacks[i] else "跟注" if call else "过牌",
                                 call, s.bets[i]+call, bool(call and call==s.stacks[i])))
        minimum, maximum = s.min_completion_betting_or_raising_to_amount, s.max_completion_betting_or_raising_to_amount
        if minimum is not None and maximum is not None:
            amounts = {((minimum+self.chip_unit-1)//self.chip_unit)*self.chip_unit: "最小加注" if max(s.bets) else "最小下注"}
            pot = s.total_pot_amount
            if self.street == "preflop":
                base = max(s.bets) if max(s.bets)>self.bb else self.bb
                for factor in (Fraction(2),Fraction(5,2),Fraction(3),Fraction(4)):
                    value = int(base*factor)
                    amounts[round(value/self.chip_unit)*self.chip_unit] = f"{float(factor):g}×{'当前下注' if base>self.bb else 'BB'}"
            else:
                for factor,label in ((Fraction(1,3),"1/3 池"),(Fraction(1,2),"半池"),(Fraction(3,4),"3/4 池"),(Fraction(1),"满池"),(Fraction(3,2),"1.5 倍池")):
                    value = s.bets[i]+call+round(factor*(pot+call))
                    amounts[round(value/self.chip_unit)*self.chip_unit] = label
            amounts[maximum] = "全下" if maximum==s.bets[i]+s.stacks[i] else "最大有效加注"
            for target,label in sorted(amounts.items()):
                if s.can_complete_bet_or_raise_to(target):
                    result.append(Action(f"raise_to_{target}", "raise", label, target-s.bets[i], target,
                                         target-s.bets[i]==s.stacks[i]))
        return result

    def observation(self, history_stats: dict | None = None) -> dict:
        snap = self.snapshot(reveal=False, hero=self.actor)
        # No names, endpoint identities, private seed or other decision distributions.
        for seat in snap["seats"]:
            seat.pop("name", None)
        snap["history"] = [{k:v for k,v in a.items() if k!="decision"} for a in self.actions]
        snap["rules"] = {"variant":"No-limit Texas Hold'em", "mode":self.mode,
                         "money_unit":"integer chips (cash: 100 chips = 1 virtual unit)",
                         "objective":"maximize bankroll profit" if self.mode=="cash" else "finish at the best possible elimination rank; no prizes or ICM payouts",
                         "ante_rule":"Big blind only; blind first when short; no rake"}
        snap["public_statistics"] = history_stats or {}
        aliases={seat["id"]:f"player_{i+1}" for i,seat in enumerate(self.seats)}
        def anonymous(value):
            if isinstance(value,dict):
                return {aliases.get(k,k):anonymous(v) for k,v in value.items()}
            if isinstance(value,list):
                return [anonymous(v) for v in value]
            return aliases.get(value,value) if isinstance(value,str) else value
        return anonymous(snap)

    def request(self, model: str, stats: dict | None = None) -> dict:
        actions = self.legal()
        criteria = {a.id:{"action":a.kind,"pay_now":a.pay,"total_street_contribution":a.to,
                          "all_in":a.all_in} for a in actions}
        return {"model":model, "state":self.observation(stats), "questions":{"action":{
            "type":"choice", "instructions":"Choose the legal poker action with the best expected strategic outcome under the stated rules and objective. Return preference probabilities over ALL offered actions. Use only information available to the acting player. Opponents' unknown hole cards are not known. These probabilities are action preferences, not hand winning probabilities.",
            "criteria":criteria}}}

    def apply(self, action_id: str, decision: dict | None = None):
        actions = {a.id:a for a in self.legal()}
        if self.manual and action_id.startswith("raise_to_") and self.actor is not None:
            target=int(action_id.removeprefix("raise_to_"))
            i=self.state.actor_index
            if self.state.can_complete_bet_or_raise_to(target):
                actions[action_id]=Action(action_id,"raise","加注",target-self.state.bets[i],target,
                                          target-self.state.bets[i]==self.state.stacks[i])
        if action_id not in actions:
            raise ValueError("不是当前合法动作")
        action = actions[action_id]
        pid, street = self.actor, self.street
        raises = sum(a["kind"]=="raise" and a["street"]==street for a in self.actions)
        event = {**action.json(),"player":pid,"street":street,"sequence":len(self.actions),
                 "raise_number":raises+2 if street=="preflop" and action.kind=="raise" else raises+1 if action.kind=="raise" else None}
        if decision:
            event["decision"] = decision
        self.committed[pid] += action.pay
        if action.kind == "fold":
            self.folded.add(pid)
            self.state.fold()
        elif action.kind in ("check","call"):
            self.state.check_or_call()
        else:
            self.state.complete_bet_or_raise_to(action.to)
        self.actions.append(event)
        self._capture("action", action.label, event)
        self.advance()

    def advance(self):
        if self.manual:
            return
        for _ in range(12):
            if not self.state.status or self.actor is not None:
                break
            if self.state.can_burn_card():
                self.state.burn_card()
            if self.state.can_deal_board():
                op = self.state.deal_board()
                self._capture("board", "公共牌 " + " ".join(repr(c) for c in op.cards))
            else:
                break
        if not self.state.status and self.events[-1]["type"] != "settlement":
            self._capture("settlement", "本手结算")

    def add_board(self, cards: list[str]):
        if not self.manual or self.actor is not None or not self.state.status:
            raise ValueError("当前不是发公共牌的时点")
        if self.state.can_burn_card():
            self.state.burn_card("??")
        expected = self.state.board_dealing_count
        if expected != len(cards):
            raise ValueError(f"当前应发 {expected} 张公共牌")
        self.state.deal_board("".join(cards))
        self._capture("board", "公共牌 " + " ".join(cards))

    def snapshot(self, reveal: bool = False, hero: str | None = None) -> dict:
        s = self.state
        # Killing a losing hand clears PokerKit's live card statuses. Cards
        # already shown remain public for the rest of this hand, including settlement.
        shown={}
        for op in s.operations:
            if isinstance(op,HoleCardsShowingOrMucking):
                shown.setdefault(op.player_index,set()).update(repr(c) for c in op.hole_cards)
        rows = []
        for pos,seat in enumerate(self.seats):
            pid = seat["id"]
            if pid in self.ids:
                i = self.ids.index(pid)
                public_cards=[c if c in shown.get(i,set()) or (j<len(s.hole_card_statuses[i]) and s.hole_card_statuses[i][j]) else "??"
                              for j,c in enumerate(self.original_holes[pid])]
                row = {"id":pid,"name":seat.get("name",pid),"seat":pos,"stack":s.stacks[i],
                       "bet":s.bets[i],"committed":self.committed[pid],"folded":pid in self.folded,
                       "all_in":s.status and s.stacks[i]==0 and pid not in self.folded,
                       "cards":self.original_holes[pid] if reveal or pid==hero else public_cards,
                       "public_cards":public_cards,
                       "position":"BTN/SB" if len(self.ids)==2 and pos==self.button else "BTN" if pos==self.button else "SB" if i==self.sb_index else "BB" if i==self.bb_index else "",
                       "payoff":s.payoffs[i] if not s.status else None}
            else:
                row = {**seat,"seat":pos,"stack":0,"bet":0,"committed":0,"folded":True,
                       "all_in":False,"cards":[],"public_cards":[],"position":"OUT","payoff":0}
            rows.append(row)
        board = [repr(c) for group in s.board_cards for c in group]
        return {"hand_number":self.number,"street":self.street,"button":self.button,
                "actor":self.actor,"complete":not s.status,"board":board,
                "seats":rows,"pot":s.total_pot_amount,
                "pots":[{"amount":p.unraked_amount,"eligible":[self.ids[i] for i in p.player_indices]} for p in s.pots],
                "small_blind":self.sb,"big_blind":self.bb,"ante":self.ante,
                "to_call":s.checking_or_calling_amount or 0,
                "legal_actions":[a.json() for a in self.legal()]}

    def _capture(self, kind: str, label: str, action: dict | None = None):
        self.events.append({"index":len(self.events),"type":kind,"label":label,"action":action,
                            "snapshot":self.snapshot(reveal=True)})

    def record(self):
        return {"spec":self.spec,"actions":self.actions,"events":self.events,"complete":not self.state.status,"analysis":getattr(self,"analysis",None)}

    @classmethod
    def restore(cls, record: dict):
        hand = cls(**record["spec"])
        for action in record["actions"]:
            hand.apply(action["id"], action.get("decision"))
        hand.analysis=record.get("analysis")
        return hand


def observer_record(record: dict, hero: str | None = None, omniscient: bool = False) -> dict:
    """Do not ship hidden cards/decisions to clients just to hide them with CSS."""
    result = {"complete":record["complete"],"events":deepcopy(record["events"]),
              "analysis":deepcopy(record.get("analysis")) if record["complete"] else None}
    for event in result["events"]:
        for seat in event["snapshot"]["seats"]:
            if not (record["complete"] and omniscient) and seat["id"] != hero:
                seat["cards"] = seat["public_cards"]
        action = event.get("action")
        if action and not record["complete"]:
            action.pop("decision", None)
    return result
