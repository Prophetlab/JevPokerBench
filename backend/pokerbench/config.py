from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
BLINDS = [(50,100,0),(75,150,0),(100,200,200),(150,300,300),(200,400,400),
          (300,600,600),(400,800,800),(500,1000,1000),(600,1200,1200),
          (800,1600,1600),(1000,2000,2000),(1500,3000,3000),(2000,4000,4000),
          (3000,6000,6000),(4000,8000,8000)]
ROSTER = [
    ("jev", "Jev", "#176b54"), ("semif", "SemIf", "#487dc0"),
    ("nanojev", "NanoJev", "#c47b3b"), ("jevlike", "jevlike", "#9672ab"),
    ("kev", "kev", "#cb686d"), ("simple-jev", "simple-jev", "#4e9b9a"),
    ("openjev-sglang", "openjev-sglang", "#968546"),
    ("gemini", "Gemini 3.8 Flash", "#597aaf"),
    ("deepseek", "DeepSeek V4.1 Flash", "#4455ae"),
    ("luna", "GPT-5.6 Luna", "#6a7772"),
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    jev_api_key: str = ""
    pokerbench_budget_cny: float = 10000
    pokerbench_admin_token: str = ""
    pokerbench_invite_code: str = ""
    pokerbench_custom_proxy_url: str = ""
    pokerbench_data_dir: str = str(ROOT / "data")


class Entry(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")
    name: str
    color: str = "#176b54"
    provider: Literal["deepseek", "openai_compatible", "jev", "systemone", "human"] = "deepseek"
    model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com"
    key_env: str = "DEEPSEEK_API_KEY"
    structured_outputs: bool = False
    reasoning_effort: Literal["low", "non-thinking"] | None = None
    reasoning_parameter: Literal["reasoning_effort", "reasoning"] = "reasoning_effort"
    abstention_policy: Literal["reject", "condition_on_legal_actions"] = "reject"
    proxy: bool = True
    input_cny_per_million: float = Field(default=2.16, ge=0)
    output_cny_per_million: float = Field(default=8.64, ge=0)
    revision: str = "unconfigured"
    credential_id: str | None = None
    api_format: Literal['chat_completions', 'responses', 'anthropic'] = 'chat_completions'
    expected_model: str | None = None
    expected_revision: str | None = None

    @model_validator(mode="after")
    def reasoning_supported(self):
        if self.reasoning_effort=="non-thinking" and self.provider!="deepseek":
            raise ValueError("non-thinking 仅支持 DeepSeek")
        if self.reasoning_effort and self.provider not in ("deepseek","openai_compatible"):
            raise ValueError("reasoning_effort 仅用于官方 adapter 的聊天模型")
        return self


def default_entries() -> list[Entry]:
    return [Entry(id=i, name=n, color=c) for i, n, c in ROSTER]


class RunConfig(BaseModel):
    name: str = Field(default="十席端点测试", min_length=1, max_length=100)
    mode: Literal["cash", "sng"] = "cash"
    human_player_id: str | None = None
    billing_mode: Literal["hosted","personal"] = "hosted"
    max_hands: int = Field(default=10, ge=1, le=100000)
    seed: int = Field(default=20260920, ge=0, le=2**63-1)
    bankroll: int = Field(default=1000000, ge=1)  # cents, 10,000 virtual units
    buy_in: int = Field(default=20000, ge=1)
    cash_small_blind: int = Field(default=50, ge=1)
    cash_big_blind: int = Field(default=100, ge=1)
    settle_every: int = Field(default=500, ge=1)
    sng_starting_stack: int = Field(default=20000, ge=1)
    level_every: int = Field(default=200, ge=1)
    blind_levels: list[tuple[int, int, int]] = Field(default_factory=lambda: BLINDS.copy())
    shuffle_each_orbit: bool = True
    entries: list[Entry] = Field(default_factory=default_entries, min_length=2, max_length=10)
    run_budget_cny: float = Field(default=20, gt=0, le=10000)
    decision_timeout: float = Field(default=120, ge=10, le=600)
    max_output_tokens: int = Field(default=8192, ge=1024, le=131072)

    @model_validator(mode="after")
    def consistent(self):
        humans=[e.id for e in self.entries if e.provider=="human"]
        if humans!=([self.human_player_id] if self.human_player_id else []):
            raise ValueError("自组局必须有且仅有一个匹配的真人席位")
        if self.buy_in > self.bankroll:
            raise ValueError("买入不能超过总资产")
        if self.cash_small_blind > self.cash_big_blind:
            raise ValueError("小盲不能大于大盲")
        if len({e.id for e in self.entries}) != len(self.entries):
            raise ValueError("参赛席位 ID 不可重复")
        if not self.blind_levels or any(sb < 1 or bb < sb or ante < 0 for sb,bb,ante in self.blind_levels):
            raise ValueError("盲注表无效")
        return self

    def blinds(self, completed_hands: int) -> tuple[int, int, int]:
        if self.mode == "cash":
            return self.cash_small_blind, self.cash_big_blind, 0
        # User asked for the supplied schedule only: hold its last level.
        return self.blind_levels[min(completed_hands // self.level_every, len(self.blind_levels)-1)]
