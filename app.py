import os
import streamlit as st
import pickle
import networkx as nx
import folium
from streamlit_folium import st_folium
import gzip

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
if search_pressed:
    if st.session_state.start_coords and st.session_state.end_coords:
        with st.spinner('선택하신 시나리오의 최적 경로를 계산 중입니다...'):
            try:
                start_node = get_nearest_node(st.session_state.start_coords[0], st.session_state.start_coords[1], G)
                end_node = get_nearest_node(st.session_state.end_coords[0], st.session_state.end_coords[1], G)

                path = nx.shortest_path(G, source=start_node, target=end_node, weight=selected_weight)

                # 계산된 경로를 메모리(session_state)에 영구 저장!
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