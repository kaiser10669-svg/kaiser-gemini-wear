# -*- coding: utf-8 -*-
"""
=============================================================================
Скрипт полной и постоянной активации Google Gemini (Assistant) на Xiaomi Watch 5
Авторы: Специально для сообщества 4PDA
=============================================================================
Инструкция:
1. Запустите Bluetooth-мост на телефоне (через start_phone_bridge.bat).
2. Запустите данный скрипт:
   python activate_gemini.py
   (скрипт автоматически определит Node ID часов и телефона, запишет постоянный
   профиль /gemini/user_enabled_assistant и активирует Gemini в режиме ActiveUntethered).
=============================================================================
"""

import sys
import socket
import struct
import hashlib
import base64
import time
import subprocess
import threading
import os
import argparse
import re

ASST_PKG = "com.google.android.wearable.assistant"
ASST_SIG = "18272fbe0e3a02a587759e5f9bde45ca72c41841"
GMS_PKG = "com.google.android.gms"
GMS_SIG = "38918a453d07199354f8b19af05ec6562ced5788"
MI_PKG = "com.xiaomi.wearable"
MI_SIG = "1da20e2bdfa678ae888e81e5ca0ec4744ebbbbbd"
SEARCH_PKG = "com.google.android.googlequicksearchbox"
SEARCH_SIG = "38918a453d07199354f8b19af05ec6562ced5788"

def encode_varint(n):
    res = bytearray()
    while True:
        b = n & 0x7f
        n >>= 7
        if n:
            res.append(b | 0x80)
        else:
            res.append(b)
            break
    return bytes(res)

def encode_field(field_num, wire_type, payload):
    tag = (field_num << 3) | wire_type
    return encode_varint(tag) + payload

def encode_str(field_num, s):
    b = s.encode('utf-8')
    return encode_field(field_num, 2, encode_varint(len(b)) + b)

def encode_int(field_num, val):
    return encode_field(field_num, 0, encode_varint(val))

def encode_bytes(field_num, b):
    return encode_field(field_num, 2, encode_varint(len(b)) + b)

def wrap_envelope(inner_ddof, seq=1):
    sha1_hash = hashlib.sha1(inner_ddof).digest()
    b64_hash = base64.urlsafe_b64encode(sha1_hash).decode('ascii').rstrip('=')
    ddog = (
        encode_bytes(1, inner_ddof) +
        encode_str(2, b64_hash) +
        encode_int(3, 1) +
        encode_int(4, 1) +
        encode_int(5, seq)
    )
    return struct.pack('>I', len(ddog)) + ddog

def build_data_item_pkt(node_id, path, data_bytes, seq_id=1, pkg=ASST_PKG, sig=ASST_SIG, source_node=None):
    now_ms = int(time.time() * 1000)
    uri = f"wear://{node_id}{path}"
    src = source_node if source_node else node_id
    ddpi = (
        encode_str(1, pkg) +
        encode_str(2, uri) +
        encode_bytes(4, data_bytes) +
        encode_int(5, now_ms) +
        encode_int(6, 0) +
        encode_str(7, src) +
        encode_int(8, now_ms) +
        encode_str(9, sig) +
        encode_int(10, seq_id)
    )
    inner_ddof = encode_bytes(9, ddpi)
    return wrap_envelope(inner_ddof, seq=seq_id + 1)

def build_cap_item_pkt(node_id, target_pkg, target_sig, cap, seq_id=1):
    now_ms = int(time.time() * 1000)
    path = f"/capabilities/{target_pkg}/{target_sig}/{cap}"
    uri = f"wear://{node_id}{path}"
    data_bytes = b"s\x08\x01" # STATIC capability
    ddpi = (
        encode_str(1, GMS_PKG) +
        encode_str(2, uri) +
        encode_bytes(4, data_bytes) +
        encode_int(5, now_ms) +
        encode_int(6, 0) +
        encode_str(7, node_id) +
        encode_str(9, GMS_SIG) +
        encode_int(10, seq_id)
    )
    inner_ddof = encode_bytes(9, ddpi)
    return wrap_envelope(inner_ddof, seq=seq_id + 1)

def build_rpc_request_pkt(src_node, tgt_node, path, data_bytes, req_id=1001, seq_id=1, pkg=ASST_PKG, sig=ASST_SIG, is_request=True):
    ddpg = (
        encode_int(1, req_id) +
        encode_str(2, pkg) +
        encode_str(3, sig) +
        encode_str(4, tgt_node) +
        encode_int(5, 0) +
        encode_str(6, path) +
        encode_bytes(7, data_bytes) +
        encode_str(8, src_node) +
        encode_int(10, 0) +
        encode_int(11, 1 if is_request else 0)
    )
    inner_ddof = encode_bytes(10, ddpg)
    return wrap_envelope(inner_ddof, seq=seq_id + 1)

def build_rpc_response_pkt(src_node, tgt_node, req_id, data_bytes, seq_id=1, pkg=ASST_PKG, sig=ASST_SIG):
    ddpg = (
        encode_int(1, req_id) +
        encode_str(2, pkg) +
        encode_str(3, sig) +
        encode_str(4, tgt_node) +
        encode_int(5, 0) +
        encode_str(6, "") +
        encode_bytes(7, data_bytes) +
        encode_str(8, src_node) +
        encode_int(10, 0)
    )
    inner_ddof = encode_bytes(18, ddpg)
    return wrap_envelope(inner_ddof, seq=seq_id + 1)

def parse_varint(b, i):
    val = 0
    shift = 0
    while i < len(b):
        byte = b[i]
        i += 1
        val |= (byte & 0x7f) << shift
        if not (byte & 0x80):
            break
        shift += 7
    return val, i

def parse_proto(b):
    i = 0
    res = {}
    while i < len(b):
        try:
            tag, i = parse_varint(b, i)
        except Exception:
            break
        field_num = tag >> 3
        wire_type = tag & 7
        if wire_type == 0:
            val, i = parse_varint(b, i)
            res[field_num] = val
        elif wire_type == 2:
            length, i = parse_varint(b, i)
            data = b[i:i+length]
            i += length
            res[field_num] = data
        elif wire_type == 1:
            if i + 8 <= len(b):
                val = struct.unpack('<Q', b[i:i+8])[0]
                res[field_num] = val
            i += 8
        elif wire_type == 5:
            i += 4
        else:
            break
    return res

def get_phone_bt_node_id(adb_bin, phone_adb):
    """Определяет Bluetooth Node ID телефона из последних 2 байт MAC-адреса BT."""
    try:
        out = subprocess.run([adb_bin, "-s", phone_adb, "shell", "dumpsys", "bluetooth_manager"], capture_output=True, text=True, errors="replace").stdout
        m = re.search(r'Address:\s*([0-9A-Fa-f]{2}[:-][0-9A-Fa-f]{2}[:-][0-9A-Fa-f]{2}[:-][0-9A-Fa-f]{2}[:-]([0-9A-Fa-f]{2})[:-]([0-9A-Fa-f]{2}))', out)
        if m:
            b4 = m.group(2).lower()
            b5 = m.group(3).lower()
            return f"{b4}{b5}0001"
    except Exception:
        pass
    return None

def get_watch_legacy_nodes(adb_bin, watch_adb):
    """Находит старые/мигрированные Node ID в WearableService на часах для перезаписи /gemini/user_enabled_assistant."""
    nodes = set()
    try:
        out = subprocess.run([adb_bin, "-s", watch_adb, "shell", "dumpsys", "activity", "service", "WearableService"], capture_output=True, text=True, errors="replace").stdout
        for m in re.finditer(r'mig::([0-9a-f]{8}):([0-9a-f]{8})', out):
            nodes.add((m.group(2), f"mig::{m.group(1)}:{m.group(2)}"))
    except Exception:
        pass
    return list(nodes)

def main():
    parser = argparse.ArgumentParser(description="Постоянная активация Google Gemini на Xiaomi Watch 5")
    parser.add_argument("--phone", help="IP-адрес телефона в локальной Wi-Fi сети")
    parser.add_argument("--port", type=int, default=5601, help="TCP-порт RFCOMM-моста на телефоне (по умолчанию: 5601)")
    parser.add_argument("--watch", help="IP-адрес и порт часов для ADB (например: 192.168.0.50:5555)")
    parser.add_argument("--account", help="Email Google-аккаунта, привязанного к часам")
    parser.add_argument("--adb", default="adb", help="Путь к adb (по умолчанию 'adb')")
    args = parser.parse_args()

    print("=" * 70)
    print("   АКТИВАЦИЯ GOOGLE GEMINI НА XIAOMI WATCH 5 (WEAR OS / HYPEROS)")
    print("=" * 70)

    phone_ip = args.phone or input("Введите IP-адрес телефона: ").strip()
    phone_port = args.port
    watch_adb = args.watch or input("Введите IP-адрес часов с портом ADB (например 192.168.0.50:5555): ").strip()
    account_email = args.account or input("Введите Google Email на часах: ").strip()
    adb_bin = args.adb

    print(f"\n>>> 1. Пробуждение часов и запуск главного экрана Gemini ({watch_adb})...")
    subprocess.run([adb_bin, "-s", watch_adb, "shell", "input keyevent 224; am start -n com.google.android.wearable.assistant/.MainActivity"], capture_output=True)
    time.sleep(1.0)

    print(f">>> 2. Подключение к RFCOMM-мосту на смартфоне ({phone_ip}:{phone_port})...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(15.0)
    try:
        sock.connect((phone_ip, phone_port))
    except Exception as e:
        print(f"\n[ОШИБКА] Не удалось подключиться к мосту {phone_ip}:{phone_port}!")
        print("Убедитесь, что вы запустили мост на смартфоне через start_phone_bridge.bat!")
        print(f"Детали ошибки: {e}")
        return

    watch_node_id = ""
    try:
        hdr = sock.recv(4)
        if len(hdr) == 4:
            msg_len = struct.unpack('>I', hdr)[0]
            raw_pkt = b''
            while len(raw_pkt) < msg_len:
                chunk = sock.recv(min(4096, msg_len - len(raw_pkt)))
                if not chunk:
                    break
                raw_pkt += chunk
            env = parse_proto(raw_pkt)
            inner = parse_proto(env.get(1, b''))
            if 7 in inner:
                ddlx = parse_proto(inner[7])
                if 1 in ddlx:
                    watch_node_id = ddlx[1].decode('utf-8', errors='ignore')
                    print(f">>> Определен сетевой ID часов (Watch Node ID): {watch_node_id}")
    except Exception as e:
        print(f">>> Чтение приветственного пакета завершено ({e}), продолжаем...")

    companion_node_id = get_phone_bt_node_id(adb_bin, f"{phone_ip}:5555") or "b22a0001"
    legacy_nodes = get_watch_legacy_nodes(adb_bin, watch_adb)
    print(f">>> Идентификатор телефона (Companion Node ID): {companion_node_id}")

    print(f">>> 3. Отправка Handshake ConnectPacket...")
    companion_ddlx = (
        encode_str(1, companion_node_id) +
        encode_str(2, "Android Companion") +
        encode_int(4, 2) +
        encode_int(5, 21) +
        encode_int(6, 0) +
        encode_int(9, 0) +
        encode_int(11, 36) +
        encode_int(12, 0)
    )
    sock.sendall(wrap_envelope(encode_bytes(7, companion_ddlx), seq=1))

    running = True
    seq_counter = 100

    def recv_loop():
        nonlocal seq_counter
        sock.settimeout(1.0)
        while running:
            try:
                r_len_bytes = sock.recv(4)
                if not r_len_bytes:
                    break
                r_len = struct.unpack('>I', r_len_bytes)[0]
                r_payload = b''
                while len(r_payload) < r_len:
                    chunk = sock.recv(min(4096, r_len - len(r_payload)))
                    if not chunk:
                        break
                    r_payload += chunk
                env = parse_proto(r_payload)
                inner = env.get(1)
                if inner:
                    inner_parsed = parse_proto(inner)
                    for f_num, f_val in inner_parsed.items():
                        if f_num in (10, 16, 18):
                            rpc = parse_proto(f_val)
                            req_id = rpc.get(1)
                            path = rpc.get(6, b'')
                            data = rpc.get(7, b'')
                            print(f"\n[RPC от часов] type={f_num} req_id={req_id} path={path} len={len(data) if data else 0}")
                            if path and b"/settingssync" in path and watch_node_id:
                                seq_counter += 1
                                resp_pkt = build_rpc_response_pkt(companion_node_id, watch_node_id, req_id, encode_int(1, 0), seq_id=seq_counter)
                                sock.sendall(resp_pkt)
            except socket.timeout:
                continue
            except Exception:
                break

    t = threading.Thread(target=recv_loop, daemon=True)
    t.start()
    time.sleep(0.5)

    print(">>> 4. Регистрация Capability и постоянных настроек /gemini/user_enabled_assistant...")
    capabilities = [
        (ASST_PKG, ASST_SIG, "assistant_settings"),
        (ASST_PKG, ASST_SIG, "joker_supported"),
        (SEARCH_PKG, SEARCH_SIG, "assistant_settings"),
        (SEARCH_PKG, SEARCH_SIG, "handle_remote_intent"),
        (MI_PKG, MI_SIG, "handle_remote_intent"),
        (GMS_PKG, GMS_SIG, "handle_remote_intent"),
    ]
    for p, s, c in capabilities:
        seq_counter += 1
        sock.sendall(build_cap_item_pkt(companion_node_id, p, s, c, seq_id=seq_counter))
        for old_host, _ in legacy_nodes:
            seq_counter += 1
            sock.sendall(build_cap_item_pkt(old_host, p, s, c, seq_id=seq_counter))
        time.sleep(0.02)

    # ВАЖНО: Поле 1 в Luoy (/gemini/user_enabled_assistant) ОБЯЗАНО содержать email аккаунта!
    # Если там пустая строка или другой аккаунт, JokerModeManagerImpl (Lotm) деактивирует Gemini при рестарте.
    uoy_payload = (
        encode_str(1, account_email) +
        encode_bytes(3, encode_int(1, 1)) +
        encode_bytes(5, encode_int(1, 1)) +
        encode_bytes(6, encode_int(1, 1))
    )
    gemini_items = [
        ("/gemini/opt_in_status", encode_int(1, 1)),
        ("/gemini/classic_features_status", encode_int(1, 1)),
        ("/gemini/locale", encode_str(1, "ru-RU")),
        ("/gemini/voice_name", encode_str(1, "")),
        ("/gemini/user_enabled_assistant", uoy_payload),
    ]
    target_pkgs = [
        (ASST_PKG, ASST_SIG),
        (SEARCH_PKG, SEARCH_SIG),
        (GMS_PKG, GMS_SIG),
        (ASST_PKG, "d9c066175f021c19df6d899697fc88a6b6c9e4f5"),
    ]
    target_nodes = [(companion_node_id, companion_node_id)]
    for old_host, mig_src in legacy_nodes:
        target_nodes.append((old_host, companion_node_id))
        target_nodes.append((old_host, old_host))
        target_nodes.append((old_host, mig_src))

    for pkg_name, pkg_sig in target_pkgs:
        for host_node, src_node in target_nodes:
            for path, data in gemini_items:
                seq_counter += 1
                sock.sendall(build_data_item_pkt(host_node, path, data, seq_id=seq_counter, pkg=pkg_name, sig=pkg_sig, source_node=src_node))
                time.sleep(0.015)

    time.sleep(0.5)

    chk0 = subprocess.run([adb_bin, "-s", watch_adb, "shell", "dumpsys activity service com.google.android.wearable.assistant | grep -E 'ActivationState|Current state'"], capture_output=True, text=True).stdout
    if "ActivationState: 3" in chk0 or "Inactive" in chk0 or "ActivationState: 2" in chk0:
        print(">>> Перевод часов в состояние ActivatingState...")
        subprocess.run([adb_bin, "-s", watch_adb, "shell", "input tap 240 428"], capture_output=True)
        time.sleep(1.2)

    print(f">>> 5. Отправка криптографического запроса /assistant/psk для {account_email}...")
    psk_bytes = os.urandom(32)
    uov = (
        encode_bytes(1, psk_bytes) +
        encode_int(2, 1) +
        encode_str(3, account_email) +
        encode_int(4, 1)
    )
    seq_counter += 1
    sock.sendall(build_rpc_request_pkt(companion_node_id, watch_node_id, "/assistant/psk", uov, req_id=9001, seq_id=seq_counter, is_request=True))
    time.sleep(1.0)

    print(f">>> 6. Отправка запроса активации /assistant/activate для {account_email}...")
    seq_counter += 1
    sock.sendall(build_rpc_request_pkt(companion_node_id, watch_node_id, "/assistant/activate", uoy_payload, req_id=9002, seq_id=seq_counter, is_request=True))
    time.sleep(1.5)

    print("\n>>> 7. Проверка статуса активации на часах...")
    chk = subprocess.run([adb_bin, "-s", watch_adb, "shell", "dumpsys", "activity", "service", "com.google.android.wearable.assistant"], capture_output=True, text=True, errors="replace")
    for line in chk.stdout.splitlines():
        if "ActivationState" in line or "Current state" in line or "UASM state" in line:
            print("   ->", line.strip())

    if "ActivationState: 0" in chk.stdout:
        print("\n" + "=" * 70)
        print("🎉 УСПЕХ! Google Gemini активирован постоянно (ActiveUntethered)!")
        print("   Статус сохраняется после перезапуска приложения и перезагрузки часов.")
        print("=" * 70)
    else:
        print("\nПроверьте экран часов — нажмите 'Начать' на экране часов и повторите запуск.")

    running = False
    sock.close()

if __name__ == '__main__':
    main()
