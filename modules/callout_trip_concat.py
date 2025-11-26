import pandas as pd
from datetime import timedelta
import re
from collections import defaultdict
import os
import glob


def callout_trip_concat(callout_info_path, transit_time_path, output_dir, min_chain_len=10):
    """
    外叫行程拼接：读取行程文件与调车时间，构建拼接图，拆分多条最长拼接链，保存拼接结果。
    """
    # 如果文件夹不存在，会创建；如果存在，则清空其中所有csv文件
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    else:
        # 清空目标文件夹中所有csv文件
        csv_files = glob.glob(os.path.join(output_dir, "*.csv"))
        for f in csv_files:
            try:
                os.remove(f)
                print(f"删除旧文件：{f}")
            except Exception as e:
                print(f"删除文件 {f} 失败：{e}")
    df = pd.read_csv(callout_info_path)
    transit_time_df = pd.read_csv(transit_time_path, delimiter=',', header=0)

    # 预处理调车时间表去重，保存覆盖（选做）
    transit_time_df = transit_time_df.drop_duplicates(subset=['LANE'])
    transit_time_df.to_csv(transit_time_path, index=False)
    print(f"调车时间表去重处理完成，已保存至 {transit_time_path}")

    # 提取重量字段
    def extract_weight(equipment_id):
        m = re.search(r'(\d+T)', str(equipment_id))
        return m.group(1) if m else None

    # 检查 `weight` 列是否存在，如果不存在则创建
    if 'weight' not in df.columns:
        print("Column 'weight' not found, creating it from 'EQUIPMENT ID'...")
        df['weight'] = df.apply(
            lambda row: extract_weight(row['EQUIPMENT ID']),
            axis=1
        )
    else:
        # 如果 `weight` 列存在，但有缺失值，则补充缺失值
        df['weight'] = df['weight'].fillna(df.apply(
            lambda row: extract_weight(row['EQUIPMENT ID']),
            axis=1
        ))

    df['SHIPMENT GATE IN SOURCE'] = pd.to_datetime(df['SHIPMENT GATE IN SOURCE'])
    df['SHIPMENT GATE OUT DESTINATION'] = pd.to_datetime(df['SHIPMENT GATE OUT DESTINATION'], format='mixed', errors='raise')

    lane_time_dict = dict(zip(transit_time_df['LANE'], transit_time_df['Lane Hrs']))
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
        if lane_dist >= 800:  # 调车距离限制
            return False
        adjusted_out = trip1['SHIPMENT GATE OUT DESTINATION'] + timedelta(hours=lane_hours)
        if adjusted_out > trip2['SHIPMENT GATE IN SOURCE']:
            return False
        return True

    def can_connect_prefer(i, j):
        trip1 = df.loc[i]
        trip2 = df.loc[j]
        if trip2['SHIPMENT GATE IN SOURCE'] < trip1['SHIPMENT GATE OUT DESTINATION']:
            return False
        if trip1['DESTINATION CITY'] != trip2['SOURCE CITY']:
            return False
        # 优先条件允许调车时间视为0
        if trip1['SHIPMENT GATE OUT DESTINATION'] > trip2['SHIPMENT GATE IN SOURCE']:
            return False
        return True

    graph_prefer = [[] for _ in range(n)]
    graph_normal = [[] for _ in range(n)]

    for key, indices in grouped.items():
        sz = len(indices)
        for i in range(sz):
            idx_i = indices[i]
            for j in range(i + 1, sz):
                idx_j = indices[j]
                if df.loc[idx_j]['SHIPMENT GATE IN SOURCE'] < df.loc[idx_i]['SHIPMENT GATE OUT DESTINATION']:
                    continue
                if can_connect_prefer(idx_i, idx_j):
                    graph_prefer[idx_i].append(idx_j)
                elif can_connect_normal(idx_i, idx_j):
                    graph_normal[idx_i].append(idx_j)

    def find_max_distance_path_consider_priority(prefer_graph, normal_graph, nodes_subset):
        nodes = list(nodes_subset)
        index_in_sub = {node: i for i, node in enumerate(nodes)}
        m = len(nodes)

        subgraph = [[] for _ in range(m)]
        for i, u in enumerate(nodes):
            for v in prefer_graph[u]:
                if v in nodes_subset:
                    subgraph[i].append(index_in_sub[v])
            if not subgraph[i]:
                for v in normal_graph[u]:
                    if v in nodes_subset:
                        subgraph[i].append(index_in_sub[v])

        weights = [df.loc[node, 'SHIPMENT DISTANCE'] for node in nodes]
        dp = weights[:]
        prev = [-1] * m

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

    uncovered_nodes = set(range(n))
    all_chains = []
    total_distance = 0

    while uncovered_nodes:
        max_dist, chain = find_max_distance_path_consider_priority(graph_prefer, graph_normal, uncovered_nodes)
        all_chains.append(chain)
        total_distance += max_dist
        uncovered_nodes -= set(chain)

    print(f"共拆分为 {len(all_chains)} 条拼接链，总距离和为 {total_distance:.2f}")

    valid_chains = [chain for chain in all_chains if len(chain) > min_chain_len]
    print(f"其中长度大于{min_chain_len}的链有 {len(valid_chains)} 条")

    # 保存结果
    os.makedirs(output_dir, exist_ok=True)
    for idx, chain in enumerate(valid_chains, 1):
        chain_df = df.loc[chain].copy()
        chain_df['拼接顺序'] = range(1, len(chain_df) + 1)
        chain_df.to_csv(f'{output_dir}/trip_chain_{idx}.csv', index=False, encoding='utf-8-sig')

    print(f"筛选后的大于{min_chain_len}行链条已保存至 {output_dir} 目录。")