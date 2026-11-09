"""
검색한 닉네임 기록 + 디스코드 계정별 '내 롤 계정' 등록
- 서버(길드)마다 따로 저장
- data/history.json 파일에 저장되어 봇을 껐다 켜도 유지됨
"""

import json
import os
import time

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DATA_FILE = os.path.join(DATA_DIR, "history.json")
MAX_PER_GUILD = 200  # 서버당 저장할 최대 닉네임 수 (오래된 것부터 삭제)


class History:
    def __init__(self):
        self.data = {"guilds": {}, "users": {}}
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                self.data.update(json.load(f))
        except FileNotFoundError:
            pass
        except (json.JSONDecodeError, OSError) as e:
            print(f"검색 기록 파일을 읽지 못해 새로 시작해요: {e}")

    def _save(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, DATA_FILE)  # 저장 중 꺼져도 파일이 깨지지 않게

    @staticmethod
    def _gid(guild_id) -> str:
        return str(guild_id or "dm")

    # ---- 검색 기록 ----
    def add(self, guild_id, riot_id: str):
        """조회에 성공한 닉네임(정확한 대소문자 형태)을 기록"""
        g = self.data["guilds"].setdefault(self._gid(guild_id), {})
        key = riot_id.lower()
        entry = g.get(key, {"count": 0})
        entry.update(name=riot_id, count=entry["count"] + 1, last=time.time())
        g[key] = entry

        if len(g) > MAX_PER_GUILD:
            oldest = sorted(g, key=lambda k: g[k]["last"])[: len(g) - MAX_PER_GUILD]
            for k in oldest:
                del g[k]
        self._save()

    def remove(self, guild_id, riot_id: str) -> bool:
        g = self.data["guilds"].get(self._gid(guild_id), {})
        if g.pop(riot_id.lower(), None):
            self._save()
            return True
        return False

    def search(self, guild_id, text: str, limit: int = 25) -> list[dict]:
        """입력한 글자가 포함된 닉네임을 최근 검색순으로"""
        g = self.data["guilds"].get(self._gid(guild_id), {})
        text = text.lower().strip()
        hits = [e for k, e in g.items() if text in k]
        hits.sort(key=lambda e: e["last"], reverse=True)
        return hits[:limit]

    # ---- 내 롤 계정 ----
    def set_user(self, user_id, riot_id: str):
        self.data["users"][str(user_id)] = riot_id
        self._save()

    def get_user(self, user_id) -> str | None:
        return self.data["users"].get(str(user_id))

    def remove_user(self, user_id) -> bool:
        if self.data["users"].pop(str(user_id), None):
            self._save()
            return True
        return False
