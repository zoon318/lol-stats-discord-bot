"""
전적 분석 결과(analyze()의 반환값)를 그래프 이미지(PNG)로 그려주는 모듈
pip install matplotlib
"""

import io
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")  # 화면 없이 이미지 파일로만 그리기
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager

# ---- 한글 폰트 설정 (윈도우/맥/리눅스 순서로 찾음) ----
_KOREAN_FONTS = ["Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR",
                 "Noto Sans KR", "Noto Sans CJK JP"]
_installed = {f.name for f in font_manager.fontManager.ttflist}
for _f in _KOREAN_FONTS:
    if _f in _installed:
        plt.rcParams["font.family"] = _f
        break
else:
    print("한글 폰트를 못 찾았어요. 그래프 글자가 깨지면 NanumGothic을 설치하세요: "
          "sudo apt install fonts-nanum")
plt.rcParams["axes.unicode_minus"] = False

# ---- 색상 (디스코드 다크 테마에 맞춤) ----
BG = "#2b2d31"
PANEL = "#313338"
TEXT = "#dbdee1"
SUB = "#949ba4"
WIN = "#5383e8"
LOSS = "#e84057"
ACCENT = "#f0b232"


def _style(ax):
    ax.set_facecolor(PANEL)
    ax.tick_params(colors=SUB, labelsize=9)
    for s in ax.spines.values():
        s.set_color("#404249")
    ax.title.set_color(TEXT)


def _radar(ax, s):
    """플레이 스타일 레이더 차트 (각 지표를 '잘하는 수준' 기준값 대비 0~100으로 환산)"""
    support = s["main_pos"] == "UTILITY"
    metrics = [
        ("KDA", s["kda"] / 4.0),
        ("킬관여", s["kp"] / 0.70),
        ("딜 비중", s["dmg_share"] / (0.15 if support else 0.28)),
        ("분당 CS", (s["cs_min"] / 1.5) if support else (s["cs_min"] / 8.5)),
        ("시야", s["vision_min"] / (2.2 if support else 1.2)),
        ("생존력", 1 - (s["d"] - 2) / 8),
        ("솔로킬", s["solo_kills"] / 1.5),
    ]
    labels = [m[0] for m in metrics]
    values = [float(np.clip(m[1], 0, 1)) * 100 for m in metrics]

    angles = np.linspace(0, 2 * np.pi, len(labels), endpoint=False).tolist()
    values += values[:1]
    angles += angles[:1]

    ax.set_facecolor(PANEL)
    ax.plot(angles, values, color=ACCENT, linewidth=2)
    ax.fill(angles, values, color=ACCENT, alpha=0.25)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, color=TEXT, fontsize=10)
    ax.set_ylim(0, 100)
    ax.set_yticks([25, 50, 75, 100])
    ax.set_yticklabels([])
    ax.grid(color="#4e5058", linewidth=0.8)
    ax.spines["polar"].set_color("#4e5058")
    ax.set_title("플레이 스타일", color=TEXT, fontsize=13, pad=28)


def _kda_trend(ax, games):
    """게임별 KDA 막대 + 누적 승률 선 (왼쪽이 오래된 게임)"""
    games = list(reversed(games))  # 오래된 → 최신
    x = np.arange(1, len(games) + 1)
    kdas = [(g["k"] + g["a"]) / max(g["d"], 1) for g in games]
    colors = [WIN if g["win"] else LOSS for g in games]

    _style(ax)
    cap = 10  # 데스 0~1인 게임 때문에 막대가 너무 길어지지 않게 10에서 자름
    ax.bar(x, [min(k, cap) for k in kdas], color=colors, width=0.7)
    for xi, k in zip(x, kdas):
        if k > cap:
            ax.text(xi, cap + 0.2, f"{k:.0f}", ha="center", color=TEXT, fontsize=8)
    ax.set_ylim(0, cap + 1.2)
    ax.set_ylabel("KDA", color=SUB)
    ax.set_xlabel("게임 (오른쪽이 최신)", color=SUB)
    ax.set_xticks(x)
    ax.set_title("게임별 KDA · 누적 승률", fontsize=13)

    ax2 = ax.twinx()
    cum_wr = np.cumsum([g["win"] for g in games]) / x * 100
    ax2.plot(x, cum_wr, color=ACCENT, marker="o", markersize=4, linewidth=2)
    ax2.set_ylim(0, 100)
    ax2.axhline(50, color=SUB, linestyle="--", linewidth=0.8)
    ax2.set_ylabel("누적 승률 (%)", color=ACCENT)
    ax2.tick_params(colors=SUB, labelsize=9)
    for sp in ax2.spines.values():
        sp.set_color("#404249")


def _champions(ax, games, champ_names):
    """챔피언별 판수(승/패 누적 막대) + 승률"""
    by = defaultdict(lambda: [0, 0])  # [승, 패]
    names = {}
    for g in games:
        by[g["champ_id"]][0 if g["win"] else 1] += 1
        names[g["champ_id"]] = champ_names.get(g["champ_id"], g["champ"])
    top = sorted(by.items(), key=lambda kv: sum(kv[1]), reverse=True)[:5][::-1]

    labels = [names[cid] for cid, _ in top]
    wins = [w for _, (w, l) in top]
    losses = [l for _, (w, l) in top]
    y = np.arange(len(top))

    _style(ax)
    ax.barh(y, wins, color=WIN, label="승")
    ax.barh(y, losses, left=wins, color=LOSS, label="패")
    ax.set_yticks(y)
    ax.set_yticklabels(labels, color=TEXT, fontsize=10)
    ax.set_xlabel("판수", color=SUB)
    ax.set_title("챔피언별 전적", fontsize=13)
    for i, (w, l) in enumerate(zip(wins, losses)):
        ax.text(w + l + 0.1, i, f"{w / (w + l) * 100:.0f}%", va="center", color=TEXT, fontsize=10)
    ax.set_xlim(0, max(w + l for w, l in zip(wins, losses)) * 1.25 + 0.5)
    ax.legend(loc="lower right", facecolor=PANEL, edgecolor="#404249", labelcolor=TEXT, fontsize=9)


def _involvement(ax, games):
    """게임별 킬관여율 · 팀 내 딜 비중"""
    games = list(reversed(games))
    x = np.arange(1, len(games) + 1)

    _style(ax)
    ax.plot(x, [g["kp"] * 100 for g in games], color=WIN, marker="o", markersize=4,
            linewidth=2, label="킬관여율")
    ax.plot(x, [g["dmg_share"] * 100 for g in games], color=ACCENT, marker="s", markersize=4,
            linewidth=2, label="팀 내 딜 비중")
    for xi, g in zip(x, games):
        if not g["win"]:
            ax.axvspan(xi - 0.5, xi + 0.5, color=LOSS, alpha=0.08)
    ax.set_ylim(0, 100)
    ax.set_xticks(x)
    ax.set_ylabel("%", color=SUB)
    ax.set_xlabel("게임 (빨간 배경 = 패배)", color=SUB)
    ax.set_title("킬관여 · 딜 비중 추이", fontsize=13)
    ax.legend(loc="upper left", facecolor=PANEL, edgecolor="#404249", labelcolor=TEXT, fontsize=9)


def _render(s, champ_names, title):
    fig = plt.figure(figsize=(12, 9), facecolor=BG)
    fig.suptitle(title, color=TEXT, fontsize=16, fontweight="bold", y=0.98)

    _radar(fig.add_subplot(2, 2, 1, polar=True), s)
    _kda_trend(fig.add_subplot(2, 2, 2), s["games"])
    _champions(fig.add_subplot(2, 2, 3), s["games"], champ_names)
    _involvement(fig.add_subplot(2, 2, 4), s["games"])

    fig.tight_layout(rect=(0, 0, 1, 0.95), h_pad=3, w_pad=3)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, facecolor=BG)
    plt.close(fig)
    buf.seek(0)
    return buf


async def make_stats_chart(s, champ_names, title):
    """그리는 데 시간이 걸리므로 별도 스레드에서 실행 (봇이 멈추지 않게)"""
    import asyncio
    return await asyncio.to_thread(_render, s, champ_names, title)
