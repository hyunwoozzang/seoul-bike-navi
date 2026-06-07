import os
import streamlit as st
import pickle
import networkx as nx
import folium
from streamlit_folium import st_folium
import gzip
import requests
from datetime import datetime, timedelta
import math

# 1. 페이지 기본 설정
st.set_page_config(page_title="서울 자전거 내비게이션", layout="wide")
st.title("🚲 서울 자전거 맞춤형 내비게이션")


# 2. 압축된 두뇌 파일 로드
@st.cache_resource
def load_graph():
    file_name = 'seoul_bike_graph.pkl.gz'

    # 1) 파일이 합쳐져 있지 않다면, 쪼개진 파트들을 하나로 묶습니다.
    if not os.path.exists(file_name):
        with open(file_name, 'wb') as outfile:
            for i in range(1, 5):  # part1, part2를 순서대로 찾습니다.
                part_name = f'{file_name}_part{i}'
                if os.path.exists(part_name):
                    with open(part_name, 'rb') as infile:
                        outfile.write(infile.read())
                else:
                    break

    # 2) 하나로 합쳐진 압축 파일을 읽어옵니다.
    with gzip.open(file_name, 'rb') as f:
        return pickle.load(f)


G = load_graph()


# --- [신규 추가 1] 실시간 풍향 가져오기 함수 ---
def get_live_wind_direction():
    auth_key = "GfxNFO0sTha8TRTtLM4WXw"
    now = datetime.now() - timedelta(hours=1)
    time_str = now.strftime('%Y%m%d%H00')
    url = f"https://apihub.kma.go.kr/api/typ01/url/kma_sfctm2.php?tm={time_str}&stn=108&help=0&authKey={auth_key}"
    try:
        response = requests.get(url, timeout=3)
        for line in response.text.split('\n'):
            if not line.startswith('#') and ' 108 ' in line:
                return int(line.split()[2]) * 10
    except:
        return None  # 통신 지연 등 오류 발생 시 None 반환
    return None


# --- [신규 추가 2] 도로 방향과 맞바람을 계산하여 동적 가중치 생성 ---
def apply_dynamic_wind_weight(graph, wind_dir):
    # 자전거는 양방향 통행이므로, 맞바람과 뒷바람을 정확히 구별하기 위해 그래프를 양방향(Directed)으로 쪼갭니다.
    DG = graph.to_directed()

    for u, v, data in DG.edges(data=True):
        lon1, lat1 = u
        lon2, lat2 = v

        # 1. 도로의 진행 각도 계산 (위도/경도 기반 방위각)
        dy = lat2 - lat1
        dx = lon2 - lon1
        road_angle = math.degrees(math.atan2(dx, dy))
        if road_angle < 0:
            road_angle += 360

        # 2. 바람 방향과 내 주행 방향의 차이 계산 (0에 가까우면 맞바람, 180에 가까우면 뒷바람)
        diff = abs(road_angle - wind_dir)
        if diff > 180:
            diff = 360 - diff

        # 3. 페널티 부여: 맞바람(0도)일 때 기본 거리의 1.5배 페널티, 뒷바람(180도)일 땐 1.0배(기본) 적용
        penalty = 1.0 + (1.0 - (diff / 180.0)) * 0.5

        # 최단거리(weight_s6)를 기준으로 페널티를 곱해 실시간 코스트 생성
        base_cost = data.get('weight_s6', 15)
        data['live_wind_cost'] = base_cost * penalty

    return DG
# 3. 6가지 시나리오 맵핑
scenarios = {
    "시나리오 1: 최저 피로도 경로": "weight_s1",
    "시나리오 2: 쾌적 경로": "weight_s2",
    "시나리오 3: 전용도로 우선 경로": "weight_s3",
    "시나리오 4: 업힐 선호 경로": "weight_s4",
    "시나리오 5: 우천 시 안전 경로": "weight_s5",
    "시나리오 6: 바람 방해 최소화 경로": "weight_s6"
}


# 4. 지도 클릭 위치와 가장 가까운 교차로 탐색 함수
def get_nearest_node(lat, lon, graph):
    nearest_node = None
    min_dist = float('inf')
    for node in graph.nodes():
        n_lon, n_lat = node[0], node[1]
        dist = (n_lat - lat) ** 2 + (n_lon - lon) ** 2
        if dist < min_dist:
            min_dist = dist
            nearest_node = node
    return nearest_node


# =====================================================================
# [핵심 수정 구간] 사용자 클릭 및 경로 상태 저장소 (Session State) 초기화
# =====================================================================
if 'start_coords' not in st.session_state:
    st.session_state.start_coords = None
if 'end_coords' not in st.session_state:
    st.session_state.end_coords = None
if 'path_coords' not in st.session_state:
    st.session_state.path_coords = None  # 선이 사라지지 않게 기억할 공간 추가!

# 5. 왼쪽 사이드바 (UI 메뉴)
with st.sidebar:
    st.header("⚙️ 주행 설정")
    selected_scenario_name = st.selectbox("주행 시나리오 선택", list(scenarios.keys()))
    selected_weight = scenarios[selected_scenario_name]

    st.markdown("---")
    st.markdown("**사용 방법:**\n1. 지도에서 원하는 **출발지**를 클릭하세요.\n2. 지도에서 원하는 **도착지**를 클릭하세요.\n3. 아래 [경로 탐색] 버튼을 누르세요.")

    if st.button("🔄 출발지/도착지 다시 찍기"):
        st.session_state.start_coords = None
        st.session_state.end_coords = None
        st.session_state.path_coords = None  # 다시 찍기를 누르면 그려진 선도 삭제
        st.rerun()

    search_pressed = st.button("🚀 경로 탐색", type="primary")

# 6. 지도 초기 중심점 (서울시청 기준)
m = folium.Map(location=[37.5665, 126.9780], zoom_start=11)

if st.session_state.start_coords:
    folium.Marker(st.session_state.start_coords, popup="출발지", icon=folium.Icon(color='green')).add_to(m)
if st.session_state.end_coords:
    folium.Marker(st.session_state.end_coords, popup="도착지", icon=folium.Icon(color='red')).add_to(m)

# 7. 알고리즘 길 찾기 실행 로직
# (기존 코드의 if search_pressed: 안쪽 부분 교체)
if search_pressed:
    if st.session_state.start_coords and st.session_state.end_coords:
        with st.spinner('선택하신 시나리오의 최적 경로를 계산 중입니다...'):
            try:
                start_node = get_nearest_node(st.session_state.start_coords[0], st.session_state.start_coords[1], G)
                end_node = get_nearest_node(st.session_state.end_coords[0], st.session_state.end_coords[1], G)

                # --- [수정된 부분] 4번 시나리오 선택 시 실시간 통신 분기 ---
                # --- [수정된 부분] 6번 시나리오 선택 시 실시간 통신 분기 ---
                # 주의: 아래 문자열이 현우님이 설정하신 6번 시나리오 이름과 띄어쓰기까지 완벽히 똑같아야 합니다.
                if selected_scenario_name == "시나리오 6: 바람 방해 최소화 경로":
                    wind_dir = get_live_wind_direction()

                    if wind_dir is not None:
                        st.toast(f"🌀 기상청 실시간 풍향({wind_dir}도)을 다운로드하여 맞바람 저항을 계산합니다!", icon="🌬️")
                        live_G = apply_dynamic_wind_weight(G, wind_dir)
                        path = nx.shortest_path(live_G, source=start_node, target=end_node, weight='live_wind_cost')
                    else:
                        st.warning("현재 기상청 API 응답이 지연되어, 저장된 기본 바람 데이터를 사용합니다.")
                        path = nx.shortest_path(G, source=start_node, target=end_node, weight=selected_weight)
                else:
                    # 다른 시나리오들은 원래대로 계산
                    path = nx.shortest_path(G, source=start_node, target=end_node, weight=selected_weight)

                st.session_state.path_coords = [(node[1], node[0]) for node in path]

            except nx.NetworkXNoPath:
                st.error("두 지점을 연결하는 경로를 찾을 수 없습니다.")
            except Exception as e:
                st.error(f"경로 탐색 중 오류가 발생했습니다: {e}")
    else:
        st.warning("출발지와 도착지를 모두 지도에 클릭하여 지정해주셔야 합니다.")
# =====================================================================
# [핵심 수정 구간] 메모리에 경로가 있으면 무조건 지도에 그리기
# =====================================================================
if st.session_state.path_coords:
    folium.PolyLine(st.session_state.path_coords, color="blue", weight=6, opacity=0.8).add_to(m)

# 8. 화면에 지도 출력 및 클릭 이벤트 감지
map_data = st_folium(m, width=1200, height=700)

if map_data and map_data.get('last_clicked'):
    clicked_lat = map_data['last_clicked']['lat']
    clicked_lng = map_data['last_clicked']['lng']

    if st.session_state.start_coords is None:
        st.session_state.start_coords = (clicked_lat, clicked_lng)
        st.rerun()
    elif st.session_state.end_coords is None:
        st.session_state.end_coords = (clicked_lat, clicked_lng)
        st.rerun()