"""真實客戶端 IP 判定（限流與登入鎖定共用）。

反向代理（Zeabur／nginx）後 request.client.host 是代理位址：
  · 拿它當限流／鎖定 key → 全站共用一個桶，任何人錯 5 次授權碼
    就鎖住所有人 15 分鐘（grok 審查發現 4）。
  · 直接信 X-Forwarded-For 最左值 → 客戶端自帶 XFF 就能每次換 IP
    繞過節流，或定向鎖定受害者 IP（grok 審查發現 3）。

正確做法：只有在直連端是私有／loopback（可信代理）時才看 XFF，
且取**最右邊的非私有位址**——標準代理鏈是 append，最右非私有值是
真正連到代理的那一跳，客戶端偽造的值只會出現在更左邊，全部忽略。
"""

from __future__ import annotations

import ipaddress


def _is_private(ip: str) -> bool:
    try:
        parsed = ipaddress.ip_address(ip.strip())
        return parsed.is_private or parsed.is_loopback
    except ValueError:
        return False


def _is_valid_ip(ip: str) -> bool:
    try:
        ipaddress.ip_address(ip.strip())
        return True
    except ValueError:
        return False


def client_ip(request) -> str:
    """取真實客戶端 IP。request 只需要 .client.host 與 .headers.get。"""
    direct = request.client.host if request.client else "unknown"
    xff = request.headers.get("x-forwarded-for", "")
    if not xff or not _is_private(direct):
        return direct
    hops = [h.strip() for h in xff.split(",") if h.strip()]
    # 由右往左找第一個非私有的合法 IP——那是客戶端連到可信代理的那一跳
    for hop in reversed(hops):
        if _is_valid_ip(hop) and not _is_private(hop):
            return hop
    return direct
