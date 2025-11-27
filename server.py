import socket
import threading
import pickle
import random
import math
import time

HOST = '0.0.0.0'
PORT = 5555
WIDTH, HEIGHT = 800, 600

# 스레드 동기화를 위한 Lock 객체
data_lock = threading.Lock()

# 게임 데이터
players = {}
obstacles = []
explosion_events = [] 
kill_logs = []
obs_counter = 0
item_counter = 0
bullet_events = []
client_connections = []

BULLET_SPEED = 600 # 초당 픽셀

def spawn_obstacle():
    global obs_counter
    for _ in range(50):
        radius = random.randint(25, 50)
        x = random.randint(50 + radius, WIDTH - 50 - radius)
        y = random.randint(50 + radius, HEIGHT - 50 - radius)
        
        collision = False
        for obs in obstacles:
            if math.hypot(x - obs['x'], y - obs['y']) < radius + obs['r'] + 10:
                collision = True; break
        
        if not collision:
            obs_id = obs_counter
            obs_counter += 1
            hp = int(radius / 5) + 5 
            return {'id': obs_id, 'x': x, 'y': y, 'r': radius, 'hp': hp, 'max_hp': hp}
    return None

# 초기 장애물 생성
while len(obstacles) < 12:
    new_obs = spawn_obstacle()
    if new_obs: obstacles.append(new_obs)

def handle_client(conn, p_id):
    global players, obstacles, explosion_events, kill_logs, bullet_events, client_connections
    
    conn.send(pickle.dumps(p_id))
    
    with data_lock:
        players[p_id] = {
            'x': -1000, 'y': -1000, 
            'name': 'Guest', 'hp': 10, 'max_hp': 10, 'lv': 1.0, 
            'point': 0, 'dead': False 
        }
        client_connections.append(conn)

    while True:
        try:
            recv_data = pickle.loads(conn.recv(8192))
            if not recv_data: break
            
            reply_data = None
            with data_lock:
                current_time = time.time()
                
                # 1. 상태 동기화
                if 'me' in recv_data:
                    me = recv_data['me']
                    if p_id in players:
                        if me.get('respawn_req', False):
                            players[p_id].update({'hp': 10, 'max_hp': 10, 'lv': 1.0, 'point': 0, 'dead': False})
                        
                        players[p_id].update({
                            'x': me['x'], 'y': me['y'], 'ba': me['ba'], 'ta': me['ta'], 'c': me['c'], 'name': me['name']
                        })
                        
                        if not me.get('respawn_req', False):
                            if me['lv'] > players[p_id]['lv']:
                                 players[p_id]['hp'] = me['max_hp']
                                 players[p_id]['lv'] = me['lv']
                            players[p_id]['point'] = me['point']
                            players[p_id]['max_hp'] = me['max_hp']
                        
                        if me['is_dead']: players[p_id]['dead'] = True

                # 3. 장애물 피격
                if 'hit_obs' in recv_data:
                    target_id = recv_data['hit_obs']
                    dmg = recv_data.get('damage', 1)
                    for i, obs in enumerate(obstacles):
                        if obs['id'] == target_id:
                            obs['hp'] -= dmg
                            explosion_events.append({'id': (time.time(), random.random()), 'x': obs['x'], 'y': obs['y'], 'r': 10, 'type': 'hit', 'time': current_time})
                            
                            if obs['hp'] <= 0:
                                explosion_events.append({'id': (time.time(), random.random()), 'x': obs['x'], 'y': obs['y'], 'r': obs['r'], 'type': 'obs', 'time': current_time})
                                ox, oy = obs['x'], obs['y']
                                for pid, p in players.items():
                                    if p['dead']: continue
                                    if math.hypot(p['x'] - ox, p['y'] - oy) < obs['r'] + 45: 
                                        p['hp'] -= 5
                                        explosion_events.append({'id': (time.time(), random.random()), 'x': p['x'], 'y': p['y'], 'r': 20, 'type': 'hit', 'time': current_time})
                                        if p['hp'] <= 0:
                                            p['hp'] = 0; p['dead'] = True
                                            kill_logs.append({'msg': f"{p['name']}님이 폭발에 휘말렸습니다.", 'time': current_time + 3})
                                            explosion_events.append({'id': (time.time(), random.random()), 'x': p['x'], 'y': p['y'], 'r': 40, 'type': 'player', 'time': current_time})
                                obstacles.pop(i)
                                new_obs = spawn_obstacle()
                                if new_obs: obstacles.append(new_obs)
                            break
                
                # 4. 플레이어 피격
                if 'hit_player' in recv_data:
                    target_pid = recv_data['hit_player']
                    dmg = recv_data['damage']
                    if p_id in players:
                        attacker_name = players[p_id]['name']
                        if target_pid in players and not players[target_pid]['dead']:
                            players[target_pid]['hp'] -= dmg
                            explosion_events.append({'id': (time.time(), random.random()), 'x': players[target_pid]['x'], 'y': players[target_pid]['y'], 'r': 15, 'type': 'hit', 'time': current_time})
                            
                            if players[target_pid]['hp'] <= 0:
                                players[target_pid]['hp'] = 0; players[target_pid]['dead'] = True
                                victim_name = players[target_pid]['name']
                                reward = players[target_pid]['lv'] * 0.5
                                players[p_id]['lv'] += reward
                                kill_logs.append({'msg': f"{attacker_name}님이 {victim_name}님을 처치했습니다.", 'time': current_time + 3})
                                explosion_events.append({'id': (time.time(), random.random()), 'x': players[target_pid]['x'], 'y': players[target_pid]['y'], 'r': 40, 'type': 'player', 'time': current_time})

                if 'new_bullets' in recv_data:
                    for b in recv_data['new_bullets']:
                        b['p_id'] = p_id
                        b['time'] = current_time
                        b['ox'] = b['x']
                        b['oy'] = b['y']
                    bullet_events.extend(recv_data['new_bullets'])

                kill_logs = [log for log in kill_logs if log['time'] > current_time]
                bullet_events = [b for b in bullet_events if current_time - b.get('time', 0) < 2]
                explosion_events = [e for e in explosion_events if current_time - e.get('time', 0) < 2]
                
                for b in bullet_events:
                    elapsed = current_time - b['time']
                    dist = elapsed * BULLET_SPEED
                    rad = math.radians(b['angle'])
                    b['x'] = b['ox'] + math.cos(rad) * dist
                    b['y'] = b['oy'] - math.sin(rad) * dist
                
                reply_data = {
                    'players': players, 'obstacles': obstacles,
                    'explosions': explosion_events, 'kill_logs': kill_logs,
                    'bullets': bullet_events
                }
            
            disconnected_clients = []
            for client_conn in list(client_connections):
                try:
                    client_conn.send(pickle.dumps(reply_data))
                except socket.error:
                    disconnected_clients.append(client_conn)
            
            if disconnected_clients:
                with data_lock:
                    for client in disconnected_clients:
                        if client in client_connections:
                            client_connections.remove(client)

        except Exception as e:
            break
            
    with data_lock:
        if p_id in players: del players[p_id]
        if conn in client_connections:
            client_connections.remove(conn)
    conn.close()

def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server.bind((HOST, PORT))
    except socket.error as e:
        print(f"Server bind error: {e}")
        return # 바인딩 실패 시 서버 종료
    server.listen()
    print("Server Running...")
    
    cid = 0
    while True:
        conn, _ = server.accept()
        threading.Thread(target=handle_client, args=(conn, cid)).start()
        cid += 1