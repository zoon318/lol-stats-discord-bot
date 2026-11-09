"""
롤 전적 디스코드 봇
- /전적 닉네임#태그  → 랭크, 최근 승률, KDA, 모스트 챔피언, 주 포지션, 플레이 스타일 분석
- 데이터: Riot Games 공식 API (op.gg, lol.ps 등도 이 API 데이터를 씁니다)

필요한 것
  pip install -U discord.py python-dotenv
  같은 폴더에 .env 파일:
    DISCORD_TOKEN=디스코드_봇_토큰
    RIOT_API_KEY=RGAPI-xxxxxxxx
    GUILD_ID=서버ID1,서버ID2   (선택: 쉼표로 여러 개. 적은 서버에 슬래시 명령어가 즉시 등록됨)
"""

import asyncio
import os
import time
from collections import Counter, defaultdict
from urllib.parse import quote

import aiohttp
import discord
from discord import app_commands

from charts import make_stats_chart
from history import History

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
RIOT_API_KEY = os.environ["RIOT_API_KEY"]
# 쉼표로 구분한 서버 ID 여러 개 지원 (예: GUILD_ID=111,222,333)
GUILD_IDS = [int(g) for g in os.environ.get("GUILD_ID", "").replace(" ", "").split(",") if g.isdigit()]

PLATFORM = "kr"     # 한국 서버 (랭크, 소환사 정보)
REGION = "asia"     # 계정/매치 정보는 대륙 단위 라우팅

QUEUE_IDS = {"solo": 420, "flex": 440, "all": None}
QUEUE_NAMES = {"solo": "솔로랭크", "flex": "자유랭크", "all": "전체 게임"}

TIER_KO = {
    "IRON": "아이언", "BRONZE": "브론즈", "SILVER": "실버", "GOLD": "골드",
    "PLATINUM": "플래티넘", "EMERALD": "에메랄드", "DIAMOND": "다이아몬드",
    "MASTER": "마스터", "GRANDMASTER": "그랜드마스터", "CHALLENGER": "챌린저",
}
POS_KO = {"TOP": "탑", "JUNGLE": "정글", "MIDDLE": "미드", "BOTTOM": "원딜", "UTILITY": "서폿"}


class RiotError(Exception):
    pass


class RiotAPI:
    """Riot API 호출 + 간단한 캐시 + 429(요청 과다) 자동 재시도"""

    def __init__(self, key: str):
        self.key = key
        self.session: aiohttp.ClientSession | None = None
        self.cache: dict[str, tuple[float, object]] = {}
        self.sem = asyncio.Semaphore(5)  # 동시 요청 수 제한

    async def start(self):
        self.session = aiohttp.ClientSession(headers={"X-Riot-Token": self.key})

    async def close(self):
        if self.session:
            await self.session.close()

    async def get(self, url: str, ttl: int = 120):
        now = time.time()
        hit = self.cache.get(url)
        if hit and hit[0] > now:
            return hit[1]

        for _ in range(3):
            async with self.sem, self.session.get(url) as r:
                if r.status == 429:
                    await asyncio.sleep(int(r.headers.get("Retry-After", "2")))
                    continue
                if r.status == 404:
                    return None
                if r.status in (401, 403):
                    raise RiotError("Riot API 키가 잘못됐거나 만료됐어요. (개발용 키는 24시간마다 재발급 필요)")
                if r.status >= 400:
                    raise RiotError(f"Riot API 오류가 났어요 (HTTP {r.status})")
                data = await r.json()

            if len(self.cache) > 3000:
                self.cache.clear()
            self.cache[url] = (now + ttl, data)
            return data
        raise RiotError("요청이 너무 많아요. 잠시 후 다시 시도해 주세요.")

    # --- 엔드포인트 ---
    async def account_by_riot_id(self, name: str, tag: str):
        return await self.get(
            f"https://{REGION}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/"
            f"{quote(name)}/{quote(tag)}",
            ttl=3600,
        )

    async def league_entries(self, puuid: str):
        return await self.get(
            f"https://{PLATFORM}.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
        )

    async def match_ids(self, puuid: str, count: int, queue: int | None):
        url = (f"https://{REGION}.api.riotgames.com/lol/match/v5/matches/by-puuid/"
               f"{puuid}/ids?start=0&count={count}")
        if queue:
            url += f"&queue={queue}"
        return await self.get(url, ttl=60)

    async def match(self, match_id: str):
        # 끝난 게임 데이터는 바뀌지 않으니 하루 동안 캐시
        return await self.get(
            f"https://{REGION}.api.riotgames.com/lol/match/v5/matches/{match_id}", ttl=86400
        )


# ---------------- 분석 ----------------

def analyze(matches: list[dict], puuid: str) -> dict | None:
    games = []
    for m in matches:
        if not m:
            continue
        info = m["info"]
        me = next((p for p in info["participants"] if p["puuid"] == puuid), None)
        if not me or info["gameDuration"] < 300 or me.get("gameEndedInEarlySurrender"):
            continue  # 다시하기(리메이크) 게임 제외

        team = [p for p in info["participants"] if p["teamId"] == me["teamId"]]
        team_kills = sum(p["kills"] for p in team) or 1
        team_dmg = sum(p["totalDamageDealtToChampions"] for p in team) or 1
        minutes = info["gameDuration"] / 60

        games.append({
            "win": me["win"],
            "champ_id": me["championId"],
            "champ": me["championName"],
            "pos": me.get("teamPosition") or "",
            "k": me["kills"], "d": me["deaths"], "a": me["assists"],
            "kp": (me["kills"] + me["assists"]) / team_kills,
            "dmg_share": me["totalDamageDealtToChampions"] / team_dmg,
            "cs_min": (me["totalMinionsKilled"] + me["neutralMinionsKilled"]) / minutes,
            "vision_min": me["visionScore"] / minutes,
            "solo_kills": me.get("challenges", {}).get("soloKills", 0),
        })

    if not games:
        return None

    n = len(games)
    avg = lambda key: sum(g[key] for g in games) / n
    wins = sum(g["win"] for g in games)
    k, d, a = avg("k"), avg("d"), avg("a")

    # 모스트 챔피언
    by_champ = defaultdict(list)
    for g in games:
        by_champ[g["champ_id"]].append(g)
    most = sorted(by_champ.values(), key=len, reverse=True)[:3]

    pos_counter = Counter(g["pos"] for g in games if g["pos"])
    main_pos = pos_counter.most_common(1)[0][0] if pos_counter else ""

    return {
        "n": n, "wins": wins, "k": k, "d": d, "a": a,
        "kda": (k + a) / max(d, 1),
        "kp": avg("kp"), "dmg_share": avg("dmg_share"),
        "cs_min": avg("cs_min"), "vision_min": avg("vision_min"),
        "solo_kills": avg("solo_kills"),
        "most": most, "main_pos": main_pos, "pos_counter": pos_counter,
        "form": [g["win"] for g in games[:10]],
        "games": games,  # 게임별 데이터 (최신순) — 그래프용
    }


def play_style_tags(s: dict) -> list[str]:
    tags = []
    support = s["main_pos"] == "UTILITY"

    if s["kp"] >= 0.65:
        tags.append("🤝 한타·합류 적극형 (킬관여 높음)")
    elif s["kp"] < 0.45:
        tags.append("🧭 독자 운영형 (킬관여 낮음)")

    if s["d"] <= 3.5:
        tags.append("🛡️ 안정형 (데스 적음)")
    elif s["d"] >= 7:
        tags.append("🔥 공격적·고위험 (데스 많음)")

    if s["dmg_share"] >= 0.28 and not support:
        tags.append("💥 딜 캐리형 (팀 딜 비중 높음)")
    if s["cs_min"] >= 8 and not support:
        tags.append("🌾 파밍 집중형 (분당 CS 높음)")
    if s["vision_min"] >= (2.0 if support else 1.2):
        tags.append("👁️ 시야 장악형")
    if s["solo_kills"] >= 1.5:
        tags.append("⚔️ 라인전 강자 (솔로킬 많음)")

    wr = s["wins"] / s["n"]
    if wr >= 0.6 and s["n"] >= 10:
        tags.append("📈 최근 폼 좋음")
    elif wr <= 0.4 and s["n"] >= 10:
        tags.append("📉 최근 폼 하락")

    return tags or ["⚖️ 밸런스형"]


def format_rank(entries: list[dict] | None, queue_type: str) -> str:
    e = next((x for x in (entries or []) if x["queueType"] == queue_type), None)
    if not e:
        return "언랭"
    tier = TIER_KO.get(e["tier"], e["tier"])
    division = "" if e["tier"] in ("MASTER", "GRANDMASTER", "CHALLENGER") else f" {e['rank']}"
    total = e["wins"] + e["losses"]
    wr = e["wins"] / total * 100 if total else 0
    return f"**{tier}{division}** {e['leaguePoints']}LP\n{e['wins']}승 {e['losses']}패 ({wr:.0f}%)"


# ---------------- 디스코드 봇 ----------------

class LolBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.riot = RiotAPI(RIOT_API_KEY)
        self.champ_ko: dict[int, str] = {}
        self.history = History()

    async def setup_hook(self):
        await self.riot.start()
        await self.load_champion_names()
        if GUILD_IDS:
            # 적어 둔 서버마다 명령어를 바로 등록
            for gid in GUILD_IDS:
                guild = discord.Object(id=gid)
                self.tree.copy_global_to(guild=guild)
                try:
                    synced = await self.tree.sync(guild=guild)
                    print(f"서버 {gid}: 명령어 {len(synced)}개 등록")
                except discord.Forbidden:
                    print(f"서버 {gid}: 등록 실패 — 봇이 이 서버에 없거나 applications.commands 없이 초대됨")
                except discord.HTTPException as e:
                    print(f"서버 {gid}: 등록 실패 — {e}")
        else:
            # 서버 ID를 안 적으면 봇이 들어간 모든 서버에 등록 (반영까지 시간이 걸릴 수 있음)
            synced = await self.tree.sync()
            print(f"전체 서버용 명령어 {len(synced)}개 등록")

    async def load_champion_names(self):
        """Data Dragon에서 챔피언 한글 이름 불러오기"""
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://ddragon.leagueoflegends.com/api/versions.json") as r:
                    version = (await r.json())[0]
                url = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/ko_KR/champion.json"
                async with s.get(url) as r:
                    data = (await r.json())["data"]
            self.champ_ko = {int(c["key"]): c["name"] for c in data.values()}
        except Exception as e:
            print(f"챔피언 한글 이름 로딩 실패(영문으로 표시): {e}")

    async def close(self):
        await self.riot.close()
        await super().close()

    async def on_ready(self):
        print(f"{self.user} 로그인 완료!")


bot = LolBot()


def split_riot_id(riot_id: str) -> tuple[str, str]:
    name, _, tag = riot_id.strip().partition("#")
    return name.strip(), (tag.strip() or "KR1")


async def find_account(riot_id: str):
    name, tag = split_riot_id(riot_id)
    return await bot.riot.account_by_riot_id(name, tag)


async def lookup(interaction: discord.Interaction, riot_id: str | None, queue_key: str, count: int):
    """닉네임 → 계정, 랭크, 최근 게임 분석까지. 실패하면 메시지를 보내고 None 반환"""
    if not riot_id or not riot_id.strip():
        riot_id = bot.history.get_user(interaction.user.id)
        if not riot_id:
            await interaction.followup.send(
                "닉네임을 입력하거나, `/등록 닉네임#태그`로 내 롤 계정을 등록해 두면 닉네임 없이 바로 조회할 수 있어요.")
            return None

    try:
        account = await find_account(riot_id)
        if not account:
            name, tag = split_riot_id(riot_id)
            await interaction.followup.send(f"❌ `{name}#{tag}` 플레이어를 찾을 수 없어요. 닉네임과 태그를 확인해 주세요.")
            return None
        puuid = account["puuid"]
        # 조회 성공한 닉네임은 정확한 표기로 기록 → 다음부터 자동완성에 뜸
        bot.history.add(interaction.guild_id, f"{account['gameName']}#{account['tagLine']}")

        entries, ids = await asyncio.gather(
            bot.riot.league_entries(puuid),
            bot.riot.match_ids(puuid, count, QUEUE_IDS[queue_key]),
        )
        matches = await asyncio.gather(*(bot.riot.match(mid) for mid in ids or []))
    except RiotError as e:
        await interaction.followup.send(f"⚠️ {e}")
        return None

    return account, entries, analyze(list(matches), puuid)


QUEUE_CHOICES = [
    app_commands.Choice(name="솔로랭크", value="solo"),
    app_commands.Choice(name="자유랭크", value="flex"),
    app_commands.Choice(name="전체 게임", value="all"),
]

RIOT_ID_HELP = "닉네임#태그 — 몇 글자만 치면 전에 검색한 닉네임이 떠요. 비우면 /등록한 내 계정"


async def nickname_autocomplete(interaction: discord.Interaction, current: str):
    """닉네임 칸에 입력할 때 '내 계정' + 이 서버에서 검색했던 닉네임을 추천"""
    choices = []
    mine = bot.history.get_user(interaction.user.id)
    if mine and current.lower() in mine.lower():
        choices.append(app_commands.Choice(name=f"⭐ 내 계정: {mine}"[:100], value=mine))

    for e in bot.history.search(interaction.guild_id, current):
        if e["name"] == mine:
            continue
        label = f"{e['name']}  ·  {e['count']}회 검색"
        choices.append(app_commands.Choice(name=label[:100], value=e["name"]))
    return choices[:25]


@bot.tree.command(name="등록", description="내 롤 계정을 등록해요. 이후 /전적, /그래프에서 닉네임을 비우면 내 계정이 조회돼요")
@app_commands.rename(riot_id="닉네임")
@app_commands.describe(riot_id="내 닉네임#태그 (예: Hide on bush#KR1)")
@app_commands.autocomplete(riot_id=nickname_autocomplete)
async def register(interaction: discord.Interaction, riot_id: str):
    await interaction.response.defer(ephemeral=True)  # 나에게만 보이는 답장
    try:
        account = await find_account(riot_id)
    except RiotError as e:
        await interaction.followup.send(f"⚠️ {e}")
        return
    if not account:
        name, tag = split_riot_id(riot_id)
        await interaction.followup.send(f"❌ `{name}#{tag}` 플레이어를 찾을 수 없어요.")
        return
    display = f"{account['gameName']}#{account['tagLine']}"
    bot.history.set_user(interaction.user.id, display)
    bot.history.add(interaction.guild_id, display)
    await interaction.followup.send(f"✅ 내 계정을 `{display}`(으)로 등록했어요. 이제 `/전적`만 입력해도 바로 조회돼요.")


@bot.tree.command(name="등록해제", description="등록한 내 롤 계정을 지워요")
async def unregister(interaction: discord.Interaction):
    if bot.history.remove_user(interaction.user.id):
        await interaction.response.send_message("🗑️ 등록한 내 계정을 지웠어요.", ephemeral=True)
    else:
        await interaction.response.send_message("등록된 계정이 없어요.", ephemeral=True)


@bot.tree.command(name="기록삭제", description="이 서버의 검색 기록에서 닉네임을 지워요")
@app_commands.rename(riot_id="닉네임")
@app_commands.describe(riot_id="지울 닉네임#태그")
@app_commands.autocomplete(riot_id=nickname_autocomplete)
async def delete_history(interaction: discord.Interaction, riot_id: str):
    if bot.history.remove(interaction.guild_id, riot_id.strip()):
        await interaction.response.send_message(f"🗑️ `{riot_id}`을(를) 검색 기록에서 지웠어요.", ephemeral=True)
    else:
        await interaction.response.send_message(f"`{riot_id}`은(는) 검색 기록에 없어요.", ephemeral=True)


@bot.tree.command(name="그래프", description="최근 게임 지표를 그래프로 보여줘요")
@app_commands.rename(riot_id="닉네임", queue="큐", count="게임수")
@app_commands.describe(
    riot_id=RIOT_ID_HELP,
    queue="분석할 게임 종류 (기본: 솔로랭크)",
    count="분석할 최근 게임 수 (3~20, 기본 20)",
)
@app_commands.choices(queue=QUEUE_CHOICES)
@app_commands.autocomplete(riot_id=nickname_autocomplete)
async def graph(
    interaction: discord.Interaction,
    riot_id: str | None = None,
    queue: app_commands.Choice[str] | None = None,
    count: app_commands.Range[int, 3, 20] = 20,
):
    await interaction.response.defer()
    queue_key = queue.value if queue else "solo"
    result = await lookup(interaction, riot_id, queue_key, count)
    if not result:
        return
    account, _, s = result
    display = f"{account['gameName']}#{account['tagLine']}"

    if not s or s["n"] < 3:
        await interaction.followup.send(f"📭 `{display}`의 최근 {QUEUE_NAMES[queue_key]} 기록이 3판 미만이라 그래프를 그릴 수 없어요.")
        return

    title = f"{display} · 최근 {s['n']}게임 ({QUEUE_NAMES[queue_key]}) {s['wins']}승 {s['n'] - s['wins']}패"
    png = await make_stats_chart(s, bot.champ_ko, title)

    file = discord.File(png, filename="stats.png")
    embed = discord.Embed(title=f"📊 {display}", color=discord.Color.gold())
    embed.set_image(url="attachment://stats.png")
    embed.set_footer(text="레이더 차트는 지표별 '잘하는 수준' 기준값 대비 점수(0~100)예요")
    await interaction.followup.send(embed=embed, file=file)


@bot.tree.command(name="전적", description="롤 플레이어의 랭크, 최근 승률, 플레이 스타일을 보여줘요")
@app_commands.rename(riot_id="닉네임", queue="큐", count="게임수")
@app_commands.describe(
    riot_id=RIOT_ID_HELP,
    queue="분석할 게임 종류 (기본: 솔로랭크)",
    count="분석할 최근 게임 수 (1~20, 기본 15)",
)
@app_commands.choices(queue=QUEUE_CHOICES)
@app_commands.autocomplete(riot_id=nickname_autocomplete)
async def stats(
    interaction: discord.Interaction,
    riot_id: str | None = None,
    queue: app_commands.Choice[str] | None = None,
    count: app_commands.Range[int, 1, 20] = 15,
):
    await interaction.response.defer()  # 조회에 몇 초 걸리므로 "생각 중..." 표시
    queue_key = queue.value if queue else "solo"
    result = await lookup(interaction, riot_id, queue_key, count)
    if not result:
        return
    account, entries, s = result
    display = f"{account['gameName']}#{account['tagLine']}"
    opgg = f"https://op.gg/ko/lol/summoners/kr/{quote(account['gameName'])}-{quote(account['tagLine'])}"

    if s:
        wr = s["wins"] / s["n"]
        color = discord.Color.blue() if wr >= 0.5 else discord.Color.red()
    else:
        color = discord.Color.greyple()

    embed = discord.Embed(title=f"🎮 {display}", url=opgg, color=color)
    embed.add_field(name="솔로랭크", value=format_rank(entries, "RANKED_SOLO_5x5"), inline=True)
    embed.add_field(name="자유랭크", value=format_rank(entries, "RANKED_FLEX_SR"), inline=True)

    if not s:
        embed.add_field(name="최근 게임", value=f"최근 {QUEUE_NAMES[queue_key]} 기록이 없어요.", inline=False)
        await interaction.followup.send(embed=embed)
        return

    embed.add_field(
        name=f"최근 {s['n']}게임 ({QUEUE_NAMES[queue_key]})",
        value=(f"**{s['wins']}승 {s['n'] - s['wins']}패** ({wr * 100:.0f}%)\n"
               f"KDA {s['k']:.1f} / {s['d']:.1f} / {s['a']:.1f} (**{s['kda']:.2f}**)\n"
               f"킬관여 {s['kp'] * 100:.0f}% · 분당CS {s['cs_min']:.1f}"),
        inline=False,
    )

    form = "".join("🟦" if w else "🟥" for w in s["form"])
    embed.add_field(name="최근 흐름 (왼쪽이 최신)", value=form, inline=False)

    pos_text = " · ".join(f"{POS_KO.get(p, p)} {c}판" for p, c in s["pos_counter"].most_common(3))
    embed.add_field(name="포지션", value=pos_text or "-", inline=False)

    most_lines = []
    for games in s["most"]:
        g0 = games[0]
        cname = bot.champ_ko.get(g0["champ_id"], g0["champ"])
        w = sum(g["win"] for g in games)
        kda = sum(g["k"] + g["a"] for g in games) / max(sum(g["d"] for g in games), 1)
        most_lines.append(f"**{cname}** {len(games)}판 {w / len(games) * 100:.0f}% · KDA {kda:.2f}")
    embed.add_field(name="모스트 챔피언", value="\n".join(most_lines), inline=False)

    embed.add_field(name="플레이 스타일", value="\n".join(play_style_tags(s)), inline=False)
    embed.set_footer(text="Riot API 기준 · 제목을 누르면 OP.GG로 이동")

    await interaction.followup.send(embed=embed)


bot.run(DISCORD_TOKEN)
