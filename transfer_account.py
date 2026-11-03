# -*- coding: utf-8 -*-
"""
=============================================================================
Скрипт прямого переноса Google-аккаунта на Xiaomi Watch 5 (Wear OS / HyperOS)
Авторы: Специально для сообщества 4PDA
=============================================================================
Требования:
1. Запущенный Bluetooth RFCOMM-мост на телефоне (start_phone_bridge.bat).
2. Запущенный Android-эмулятор в Android Studio (с образом Google Play).
3. Установленный в эмулятор MiHealthBridge.apk.
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
import argparse

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

def encode_fixed64(field_num, val):
    tag = (field_num << 3) | 1
    return encode_varint(tag) + struct.pack('<Q', val)

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

def build_channel_open_pkt(src_node, tgt_node, channel_id, seq_id=100, req_id=200):
    ddlq = (
        encode_int(1, 1) + # CHANNEL_CONTROL_OPEN
        encode_fixed64(2, channel_id) +
        encode_int(3, 1) + # from_channel_opener = true
        encode_str(4, "com.xiaomi.wearable") +
        encode_str(5, "1da20e2bdfa678ae888e81e5ca0ec4744ebbbbbd") +
        encode_str(6, "/accounts/sync") +
        encode_int(7, 0) +
        encode_int(8, 0)
    )
    ddlv = (
        encode_bytes(2, ddlq) +
        encode_int(6, 1) +
        encode_int(7, 0)
    )
    ddpg = (
        encode_int(1, req_id) +
        encode_str(2, "com.xiaomi.wearable") +
        encode_str(3, "1da20e2bdfa678ae888e81e5ca0ec4744ebbbbbd") +
        encode_str(4, tgt_node) +
        encode_int(5, 0) +
        encode_str(6, "") +
        encode_str(8, src_node) +
        encode_bytes(9, ddlv) +
        encode_int(10, 0)
    )
    return wrap_envelope(encode_bytes(16, ddpg), seq=seq_id)

def build_channel_data_pkt(src_node, tgt_node, channel_id, stream_bytes, offset=0, is_final=False, seq_id=100, req_id=200):
    ddls = (
        encode_fixed64(1, channel_id) +
        encode_int(2, 1) + # from_channel_opener = true
        encode_int(3, offset)
    )
    ddlt = (
        encode_bytes(1, ddls) +
        encode_bytes(2, stream_bytes) +
        encode_int(3, 1 if is_final else 0)
    )
    ddlv = (
        encode_bytes(3, ddlt) +
        encode_int(6, 1) +
        encode_int(7, 0)
    )
    ddpg = (
        encode_int(1, req_id) +
        encode_str(2, "com.xiaomi.wearable") +
        encode_str(3, "1da20e2bdfa678ae888e81e5ca0ec4744ebbbbbd") +
        encode_str(4, tgt_node) +
        encode_int(5, 0) +
        encode_str(6, "") +
        encode_str(8, src_node) +
        encode_bytes(9, ddlv) +
        encode_int(10, 0)
    )
    return wrap_envelope(encode_bytes(16, ddpg), seq=seq_id)

def build_channel_ack_pkt(src_node, tgt_node, channel_id, from_channel_opener, ack_offset, seq_id=100, req_id=200):
    ddls = (
        encode_fixed64(1, channel_id) +
        encode_int(2, 1 if from_channel_opener else 0) +
        encode_int(3, ack_offset)
    )
    ddlr = (
        encode_bytes(1, ddls)
    )
    ddlv = (
        encode_bytes(4, ddlr) +
        encode_int(6, 1) +
        encode_int(7, 0)
    )
    ddpg = (
        encode_int(1, req_id) +
        encode_str(2, "com.xiaomi.wearable") +
        encode_str(3, "1da20e2bdfa678ae888e81e5ca0ec4744ebbbbbd") +
        encode_str(4, tgt_node) +
        encode_int(5, 0) +
        encode_str(6, "") +
        encode_str(8, src_node) +
        encode_bytes(9, ddlv) +
        encode_int(10, 0)
    )
    return wrap_envelope(encode_bytes(16, ddpg), seq=seq_id)

def build_lapl_command(account_name, account_type="com.google", cmd_type=2):
    api = encode_str(1, account_name) + encode_str(2, account_type)
    apt = encode_int(1, cmd_type) + encode_bytes(2, api)
    lapl = encode_int(1, 1) + encode_bytes(2, apt)
    return struct.pack('>I', len(lapl)) + lapl

def build_lapl_transfer_data(payload_bytes):
    laqb = encode_bytes(1, payload_bytes)
    lapl = encode_int(1, 3) + encode_bytes(4, laqb)
    return struct.pack('>I', len(lapl)) + lapl

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

running = True
watch_node_id = ''
companion_node_id = 'b22a0001'
current_channel_id = 0
channel_seq = 0
seq_counter = 100

emu_conn = None
vivo_sock = None
channel_ready = False
pending_outgoing_payloads = []

def send_to_watch_channel(raw_bytes):
    global channel_ready, pending_outgoing_payloads, vivo_sock, current_channel_id, channel_seq, seq_counter
    if not channel_ready:
        pending_outgoing_payloads.append(raw_bytes)
        return
    lapl_data = build_lapl_transfer_data(raw_bytes)
    pkt = build_channel_data_pkt(
        companion_node_id, watch_node_id, current_channel_id,
        lapl_data, offset=channel_seq, is_final=False,
        seq_id=seq_counter, req_id=seq_counter + 50
    )
    seq_counter += 1
    channel_seq += 1
    try:
        vivo_sock.sendall(pkt)
    except Exception as e:
        print(f"[Router] Send error: {e}")

def bridge_server(port=8899):
    global emu_conn, running
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('0.0.0.0', port))
    srv.listen(1)
    print(f"[BridgeServer] Ожидание подключения от MiHealthBridge на порту {port}...")
    try:
        conn, addr = srv.accept()
        print(f"[BridgeServer] MiHealthBridge подключен: {addr}")
        emu_conn = conn
        buf = bytearray()
        while running:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf.extend(chunk)
            while len(buf) >= 4:
                msg_len = struct.unpack('>I', buf[:4])[0]
                if len(buf) < 4 + msg_len:
                    break
                frame = bytes(buf[:4 + msg_len])
                buf = buf[4 + msg_len:]
                send_to_watch_channel(frame)
    except Exception as e:
        if running:
            print(f"[BridgeServer] Ошибка: {e}")
    finally:
        srv.close()

def main():
    global vivo_sock, current_channel_id, channel_seq, seq_counter, channel_ready, running, watch_node_id
    parser = argparse.ArgumentParser(description="Перенос Google-аккаунта на Xiaomi Watch 5")
    parser.add_argument("--account", required=True, help="Email переносимого Google-аккаунта")
    parser.add_argument("--phone", required=True, help="IP-адрес телефона")
    parser.add_argument("--port", type=int, default=5601, help="Порт моста на телефоне (5601)")
    parser.add_argument("--watch", required=True, help="IP-адрес часов с портом ADB (например 192.168.0.50:5555)")
    parser.add_argument("--emulator", default="emulator-5554", help="Имя adb-устройства эмулятора (emulator-5554)")
    args = parser.parse_args()

    account = args.account
    print(f"=== Перенос аккаунта {account} на Xiaomi Watch 5 ===")

    # Запускаем BridgeServer для эмулятора
    threading.Thread(target=bridge_server, args=(8899,), daemon=True).start()

    # Подключаемся к телефону
    vivo_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    vivo_sock.connect((args.phone, args.port))

    # Считываем Node ID часов
    try:
        vivo_sock.settimeout(2.0)
        hdr = vivo_sock.recv(4)
        if len(hdr) == 4:
            mlen = struct.unpack('>I', hdr)[0]
            raw = vivo_sock.recv(mlen)
            p = parse_proto(raw)
            if 7 in p:
                ddlx = parse_proto(p[7])
                if 1 in ddlx:
                    watch_node_id = ddlx[1].decode('utf-8', errors='ignore')
                    print(f">>> Определен Node ID часов: {watch_node_id}")
    except Exception:
        pass
    vivo_sock.settimeout(None)

    # Handshake ConnectPacket
    companion_ddlx = (
        encode_str(1, companion_node_id) +
        encode_str(2, "Android Companion") +
        encode_int(4, 30) +
        encode_int(8, 0) +
        encode_str(10, "00000001")
    )
    vivo_sock.sendall(struct.pack('>I', len(companion_ddlx)) + companion_ddlx)
    time.sleep(1.0)

    # Открываем канал /accounts/sync
    current_channel_id = int(time.time() * 1000) & 0x7FFFFFFF
    print(f">>> Открытие канала /accounts/sync (channel_id={current_channel_id})...")
    open_pkt = build_channel_open_pkt(companion_node_id, watch_node_id, current_channel_id, seq_id=101, req_id=201)
    vivo_sock.sendall(open_pkt)
    time.sleep(1.0)

    # Отправляем команду инициализации аккаунта
    lapl_cmd = build_lapl_command(account, "com.google", cmd_type=2)
    cmd_pkt = build_channel_data_pkt(companion_node_id, watch_node_id, current_channel_id, lapl_cmd, offset=0, seq_id=102, req_id=202)
    vivo_sock.sendall(cmd_pkt)
    channel_seq = 1
    channel_ready = True
    print(f">>> Команда переноса аккаунта отправлена на часы!")

    # Запускаем MiHealthBridge на эмуляторе
    print(">>> Запуск MiHealthBridge на эмуляторе...")
    subprocess.run(["adb", "-s", args.emulator, "shell", "am", "start", "-n", "com.mi.health/.BridgeActivity",
                    "--es", "account", account, "--es", "host", "10.0.2.2", "--ei", "port", "8899"])

    print(">>> Ожидание завершения трансфера FIDO2...")
    for _ in range(60):
        time.sleep(1.0)

    print("Процесс завершен. Проверьте 'Настройки -> Аккаунты' на часах.")

if __name__ == '__main__':
    main()
