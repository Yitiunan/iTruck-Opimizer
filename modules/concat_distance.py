import pandas as pd
from datetime import timedelta
import re
from collections import defaultdict
import os
import glob

# 1. 读取数据
callout_info_path = "../results/callout_info.csv"
transit_time_path = "../data source/调车时间.csv"

df = pd.read_csv(callout_info_path)
transit_time_df = pd.read_csv(transit_time_path, delimiter=',', header=0)

# 2. 预处理
# 对调车时间表去重
transit_time_df = transit_time_df.drop_duplicates(subset=['LANE'])
# 保存结果到新的CSV文件
output_path = '../data source/调车时间.csv'  # 替换为你希望保存的文件路径
transit_time_df.to_csv(output_path, index=False)
print(f"处理完成，结果已保存到 {output_path}")

# 清空目标文件夹内所有csv文件，避免旧数据干扰
target_folder = '../results/concat results'
if os.path.exists(target_folder):
    files = glob.glob(os.path.join(target_folder, '*.csv'))
    for f in files:
        os.remove(f)
    print(f"已删除文件夹 {target_folder} 下的 {len(files)} 个 CSV 文件。")
else:
    print(f"文件夹 {target_folder} 不存在，跳过清空操作。")
def extract_weight(equipment_id):
    m = re.search(r'(\d+T)', str(equipment_id))
    return m.group(1) if m else None

df['weight'] = df['EQUIPMENT ID'].apply(extract_weight)
df['SHIPMENT GATE IN SOURCE'] = pd.to_datetime(df['SHIPMENT GATE IN SOURCE'])
df['SHIPMENT GATE OUT DESTINATION'] = pd.to_datetime(df['SHIPMENT GATE OUT DESTINATION'])

lane_time_dict = dict(zip(transit_time_df['LANE'], transit_time_df['Lane Hrs']))
# 读取调车距离列
lane_distance_dict = dict(zip(transit_time_df['LANE'], transit_time_df['Lane_Distance']))

df = df.sort_values('SHIPMENT GATE IN SOURCE').reset_index(drop=True)
n = len(df)

grouped = defaultdict(list)
for idx, row in df.iterrows():
    key = (row['weight'], row['LOCATION'])
    grouped[key].append(idx)



def can_connect_normal(i, j):
    trip1 = df.loc[i]
    trip2 = df.loc[j]
    if trip2['SHIPMENT GATE IN SOURCE'] < trip1['SHIPMENT GATE OUT DESTINATION']:
        return False
    lane = f"{trip1['DESTINATION CITY']}_{trip2['SOURCE CITY']}"
    lane_hours = lane_time_dict.get(lane)
    lane_dist = lane_distance_dict.get(lane)
    if lane_hours is None or pd.isna(lane_hours):
        return False
    if lane_dist is None or pd.isna(lane_dist):
        return False
        # 新增调车距离限制
    if lane_dist >= 800:
        return False
    adjusted_out = trip1['SHIPMENT GATE OUT DESTINATION'] + timedelta(hours=lane_hours)
    if adjusted_out > trip2['SHIPMENT GATE IN SOURCE']:
        return False
    return True

def can_connect_prefer(i, j):
    # 优先条件：DESTINATION CITY == SOURCE CITY
    trip1 = df.loc[i]
    trip2 = df.loc[j]
    if trip2['SHIPMENT GATE IN SOURCE'] < trip1['SHIPMENT GATE OUT DESTINATION']:
        return False
    if trip1['DESTINATION CITY'] != trip2['SOURCE CITY']:
        return False
    # 时间也要满足
    # 允许调车时间视为0或忽略
    if trip1['SHIPMENT GATE OUT DESTINATION'] > trip2['SHIPMENT GATE IN SOURCE']:
        return False
    return True

# 7. 构建两种图邻接表（优先边和普通边）
graph_prefer = [[] for _ in range(n)]
graph_normal = [[] for _ in range(n)]

for key, indices in grouped.items():
    sz = len(indices)
    for i in range(sz):
        idx_i = indices[i]
        for j in range(i+1, sz):
            idx_j = indices[j]
            if df.loc[idx_j]['SHIPMENT GATE IN SOURCE'] < df.loc[idx_i]['SHIPMENT GATE OUT DESTINATION']:
                continue
            # 优先边
            if can_connect_prefer(idx_i, idx_j):
                graph_prefer[idx_i].append(idx_j)
            # 普通边（仅当不是优先边时）
            elif can_connect_normal(idx_i, idx_j):
                graph_normal[idx_i].append(idx_j)

# 8. 修改寻找最大距离路径函数，优先使用优先边，若没边可走，再使用普通边

def find_max_distance_path_consider_priority(prefer_graph, normal_graph, nodes_subset):
    nodes = list(nodes_subset)
    index_in_sub = {node: i for i, node in enumerate(nodes)}
    m = len(nodes)

    # 构建子图，先填优先边
    subgraph = [[] for _ in range(m)]
    for i, u in enumerate(nodes):
        # 优先边
        for v in prefer_graph[u]:
            if v in nodes_subset:
                subgraph[i].append(index_in_sub[v])
        # 如果优先边没出边，才用普通边补充
        if not subgraph[i]:
            for v in normal_graph[u]:
                if v in nodes_subset:
                    subgraph[i].append(index_in_sub[v])

    weights = [df.loc[node, 'SHIPMENT DISTANCE'] for node in nodes]
    dp = weights[:]
    prev = [-1]*m

    for i in range(m):
        for j in subgraph[i]:
            if dp[j] < dp[i] + weights[j]:
                dp[j] = dp[i] + weights[j]
                prev[j] = i

    max_val = max(dp)
    end_pos = dp.index(max_val)

    path_sub = []
    cur = end_pos
    while cur != -1:
        path_sub.append(nodes[cur])
        cur = prev[cur]
    path_sub.reverse()

    return max_val, path_sub

# 9. 主循环
uncovered_nodes = set(range(n))
all_chains = []
total_distance = 0

while uncovered_nodes:
    max_dist, chain = find_max_distance_path_consider_priority(graph_prefer, graph_normal, uncovered_nodes)
    all_chains.append(chain)
    total_distance += max_dist
    uncovered_nodes -= set(chain)

print(f"共拆分为 {len(all_chains)} 条拼接链，总距离和为 {total_distance:.2f}")

# 10. 只保存长度大于10的链
valid_chains = [chain for chain in all_chains if len(chain) > 10]

print(f"其中长度大于10的链有 {len(valid_chains)} 条")

for idx, chain in enumerate(valid_chains, 1):
    chain_df = df.loc[chain].copy()
    chain_df['拼接顺序'] = range(1, len(chain_df)+1)
    chain_df.to_csv(f'../results/concat results/trip_chain_{idx}.csv', index=False, encoding='utf-8-sig')

print("筛选后的大于10行链条已保存为 trip_chain_*.csv 文件。")
