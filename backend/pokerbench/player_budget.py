"""An invitation grants a lifetime five-yuan DeepSeek allowance per account."""
import hmac
import time
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

LIMIT_CNY=5.0

class PlayerBudgetExceeded(Exception):
    def __init__(self,user_id):
        self.user_id=user_id
        super().__init__('DeepSeek 邀请额度不足，请使用自己的 Key 或选择其他模型')


def setup(db):
    db.execute('CREATE TABLE IF NOT EXISTS player_budget_blocks(user_id TEXT PRIMARY KEY)')
    db.execute('CREATE TABLE IF NOT EXISTS player_logins(user_id TEXT PRIMARY KEY, last_login REAL)')
    db.execute('CREATE TABLE IF NOT EXISTS player_invites(user_id TEXT PRIMARY KEY, activated REAL NOT NULL)')
    db.commit()


def owner(db,run_id):
    if run_id.startswith('advisor/'):return run_id.split('/',1)[1]
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='room_owners'").fetchone():return None
    row=db.execute('SELECT user_id FROM room_owners WHERE run_id=?',(run_id,)).fetchone()
    return row[0] if row else None


def invited(db,user_id):
    return bool(db.execute('SELECT 1 FROM player_invites WHERE user_id=?',(user_id,)).fetchone())


def usage(db,user_id):
    row=db.execute('SELECT activated FROM player_invites WHERE user_id=?',(user_id,)).fetchone()
    if not row:return 0.0
    return float(db.execute("""SELECT COALESCE(SUM(CASE WHEN status='pending' THEN reserved ELSE charged END),0)
      FROM calls WHERE created>=? AND json_extract(body,'$.billing_user')=?
      AND json_extract(body,'$.provider')='deepseek' AND COALESCE(json_extract(body,'$.self_funded'),0)=0""",(row[0],user_id)).fetchone()[0])


def blocked(db,user_id):
    return not invited(db,user_id) or usage(db,user_id)>=LIMIT_CNY


def check_reservation(db,run_id,amount,provider='unknown',self_funded=False):
    user_id=owner(db,run_id)
    if user_id and provider=='deepseek' and not self_funded and (not invited(db,user_id) or usage(db,user_id)+amount>LIMIT_CNY):
        raise PlayerBudgetExceeded(user_id)


def block(db,user_id):
    # Reservation failure never resets or consumes the remaining credit.
    pass


def paid(entry):return entry.provider=='deepseek'


def record_login(db,user_id):
    with db:db.execute('INSERT OR REPLACE INTO player_logins VALUES(?,?)',(user_id,time.time()))


def retire_paid(run,cfg,db):
    # Credit failures use a legal fallback for this hand. Only the host removes seats,
    # except DeepSeek's explicitly configured three-timeout rule.
    return


def install_routes(app,store,accounts,settings):
    from .accounts import private_json, same_origin
    class InviteInput(BaseModel):
        code:str=Field(min_length=1,max_length=128)

    @app.get('/api/auth/budget')
    async def budget(request:Request):
        user=accounts.user(request,required=True);uid=user['uid'];active=invited(store.db,uid);spent=usage(store.db,uid)
        return private_json({'invited':active,'limit_cny':LIMIT_CNY if active else 0,'used_cny':spent,
            'remaining_cny':max(0,LIMIT_CNY-spent) if active else 0,'exhausted':active and spent>=LIMIT_CNY})

    @app.post('/api/auth/invite')
    async def invite(body:InviteInput,request:Request):
        same_origin(request);user=accounts.user(request,required=True)
        # Reuse persistent account/IP attempt throttling; do not log the code.
        accounts.throttle(request,'invite:'+user['uid'])
        now=time.time()
        if not settings.pokerbench_invite_code or not hmac.compare_digest(body.code,settings.pokerbench_invite_code):
            raise HTTPException(403,'邀请码不正确')
        with store.db:store.db.execute('INSERT OR IGNORE INTO player_invites VALUES(?,?)',(user['uid'],now))
        return await budget(request)
