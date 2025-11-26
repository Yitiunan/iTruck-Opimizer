import os
import pandas as pd
from datetime import timedelta
import re
from collections import defaultdict
import numpy as np

def delete_csv_in_subdirectories(folder_path):
    """
    删除指定文件夹下所有子目录中的 CSV 文件
    :param folder_path: 文件夹路径
    """
    for subdir, dirs, files in os.walk(folder_path):
        for file in files:
            if file.lower().endswith('.csv'):
                file_path = os.path.join(subdir, file)
                try:
                    os.remove(file_path)  # 删除文件
                    print(f"已删除文件: {file_path}")
                except Exception as e:
                    print(f"删除文件 {file_path} 时出错: {e}")

def simulate_callout_to_rental(folder_path, rental_cost_path, output_path, exchange_rate_path, dispatch_time_path):
    """
    外叫车转月租车模拟模块
    :param folder_path: 包含 CSV 文件的文件夹路径
    :param rental_cost_path: rental cost 表的路径
    :param output_path: 保存结果的路径
    """
    # 删除子目录中的所有 CSV 文件
    print("开始删除子目录中的 CSV 文件...")
    update_results_folder = os.path.join(folder_path, "update results")
    delete_csv_in_subdirectories(update_results_folder)

    # 读取 rental cost 表
    rental_cost = pd.read_csv(rental_cost_path)
    exchange_rate = pd.read_excel(exchange_rate_path)
    # special_lane_df 已经是字符串了，确认exchange_rate也转成字符串：
    exchange_rate['YYYYMM'] = pd.to_datetime(exchange_rate['YYYYMM'], errors='coerce').dt.strftime('%Y/%m')
    dispatch_time = pd.read_csv(dispatch_time_path)

    # 初始化结果列表
    results = []

    # 遍历文件夹中的所有 CSV 文件
    for file_name in os.listdir(folder_path):
        if file_name.endswith('.csv'):
            file_path = os.path.join(folder_path, file_name)
            # 读取当前 CSV 文件
            df = pd.read_csv(file_path)

            # 计算外叫车行程费用
            df['Callout_Cost'] = df.apply(
                lambda row: row['SHIPMENT TOTAL COST USD'] if row['INVOICE TYPE'] == 'PARENT' else row['TOTAL INVOICE COST (USD)'],
                axis=1
            )
            total_callout_cost = df['Callout_Cost'].sum()
            # 删除 weight 列中含有 NaN 的行
            df = df.dropna(subset=['weight'])
            # 根据 LOCATION 和车型 weight 匹配 rental cost 表
            df = pd.merge(
                df,
                rental_cost,
                left_on=['LOCATION', 'weight'],
                right_on=['Location', 'Truck plate(No DG/RA)'],
                how='left'
            )

            # 删除未匹配到的记录
            df.dropna(subset=['Rental FAIXED', 'Rental Unit rate'], inplace=True)

            # 检查是否为空
            if df.empty:
                print(f"文件 {file_name} 中所有记录均未匹配到 rental cost 表，已跳过该文件。")
                continue

            # 计算行程月份数量
            df['SHIPMENT CREATION DATE'] = pd.to_datetime(df['SHIPMENT CREATION DATE'], errors='coerce')
            min_date = df['SHIPMENT CREATION DATE'].min()
            max_date = df['SHIPMENT CREATION DATE'].max()
            months = (max_date.year - min_date.year) * 12 + max_date.month - min_date.month + 1
            # 添加 YYYYMM 列，用于匹配汇率
            df['YYYYMM'] = pd.to_datetime(
                df['SHIPMENT CREATION DATE'], errors='coerce'
            ).dt.strftime('%Y/%m')
            # 合并汇率表
            df = pd.merge(
                df,
                exchange_rate,
                on='YYYYMM',
                how='left'
            )
            if 'Dollar_RMB_rate_x' in df.columns and 'Dollar_RMB_rate_y' in df.columns:
                df['Dollar_RMB_rate'] = df['Dollar_RMB_rate_y'].fillna(df['Dollar_RMB_rate_x'])
                df = df.drop(columns=['Dollar_RMB_rate_x', 'Dollar_RMB_rate_y'])
            # 计算月租租赁费用（人民币转换为美元）
            df['Rental FAIXED(USD)'] = df['Rental FAIXED'] / df['Dollar_RMB_rate']
            rental_fixed_cost = df['Rental FAIXED(USD)'].iloc[0] * months


            # 计算里程费用
            def calculate_distance_cost(row):
                if row['SHIPMENT LANE'] in ['TIANJIN_TIANJIN', 'HUIZHOU_HUIZHOU']:
                    if row['SHIPMENT DISTANCE'] <= 100:
                        return 0  # 小于等于100的部分为0
                    else:
                        return row['SHIPMENT DISTANCE'] * row['Rental Unit rate']  # 超出100的正常计算
                if row['SHIPMENT LANE'] in ['HUIZHOU_HAIKOU', 'HAIKOU_HUIZHOU', 'ZHANJIANG_HAIKOU', 'HAIKOU_ZHANJIANG']:
                    return (row['SHIPMENT DISTANCE'] * row['Rental Unit rate']) + 2200/1.09
                return row['SHIPMENT DISTANCE'] * row['Rental Unit rate']

            df['Distance Cost'] = df.apply(calculate_distance_cost, axis=1)
            # 计算里程费用（人民币转换为美元）
            df['Distance Cost(USD)'] = df['Distance Cost'] / df['Dollar_RMB_rate']
            total_distance_cost = df['Distance Cost(USD)'].sum()

            # 计算调车费用
            def calculate_diaoche_lane_and_cost(df, dispatch_time):
                """
                根据 DESTINATION CITY 和 SOURCE CITY 生成 DIAOCHE_LANE，计算调车费用
                """
                # 初始化 DIAOCHE_LANE 列
                df['DIAOCHE_LANE'] = None

                # 从第二行开始，逐行生成 DIAOCHE_LANE
                for i in range(1, len(df)):
                    df.loc[i, 'DIAOCHE_LANE'] = f"{df.loc[i - 1, 'DESTINATION CITY']}_{df.loc[i, 'SOURCE CITY']}"

                    # 特殊规则：如果 DIAOCHE_LANE 是 TIANJIN_TIANJIN，Lane_Distance 等于上一行的 SHIPMENT DISTANCE
                    if df.loc[i, 'DIAOCHE_LANE'] == 'TIANJIN_TIANJIN':
                        # 获取上一行 SHIPMENT DISTANCE
                        prev_shipment_distance = df.loc[i, 'SHIPMENT DISTANCE'] if df.loc[i, 'SHIPMENT LANE'] == 'TIANJIN_TIANJIN' else 0
                        df.loc[i, 'Lane_Distance'] = df.loc[i, 'SHIPMENT DISTANCE']
                        # 规则：判断 Lane_Distance + prev_shipment_distance 是否超过 100
                        total_distance = df.loc[i, 'Lane_Distance'] + prev_shipment_distance
                        if total_distance <= 100:
                            df.loc[i, '调车费用'] = 0
                            df.loc[i, 'Distance Cost(USD)'] = 0
                        else:
                            # 计算调车费用
                            cost = (df.loc[i, 'Lane_Distance'] * df.loc[i, 'Rental Unit rate']) / df.loc[
                                i, 'Dollar_RMB_rate']
                            df.loc[i, '调车费用'] = cost
                            df.loc[i, 'Distance Cost(USD)'] = cost
                    else:
                        df.loc[i, 'Lane_Distance'] = np.nan  # 其余情况暂时填充为 NaN
                # 与 dispatch_time 表匹配，获取 Lane_Distance
                df = pd.merge(
                    df,
                    dispatch_time[['LANE', 'Lane_Distance']],
                    left_on='DIAOCHE_LANE',
                    right_on='LANE',
                    how='left'
                )
                # 如果原始 Lane_Distance 已有值（如 TIANJIN_TIANJIN 特殊处理的值），则优先保留原始值
                df['Lane_Distance'] = df['Lane_Distance_x'].fillna(df['Lane_Distance_y'])
                df.drop(columns=['Lane_Distance_x', 'Lane_Distance_y'], inplace=True)

                # 计算调车费用
                df['调车费用'] = np.where(
                    df['DIAOCHE_LANE'].isin(['HUIZHOU_HUIZHOU']),
                    0,  # TIANJIN_TIANJIN 和 HUIZHOU_HUIZHOU 调车费用为 0
                    (df['Lane_Distance'] * df['Rental Unit rate']) / df['Dollar_RMB_rate']  # 正常计算调车费用
                )
                chugang_condition = df['DIAOCHE_LANE'].isin(
                    ['HUIZHOU_HAIKOU', 'HAIKOU_HUIZHOU', 'ZHANJIANG_HAIKOU', 'HAIKOU_ZHANJIANG'])
                # 满足条件的行加上 2200/1.09
                df.loc[chugang_condition, '调车费用'] = df.loc[chugang_condition, '调车费用'] + 2200 / 1.09

                # 填充空值为 0
                df['调车费用'] = df['调车费用'].fillna(0)

                return df
            df = calculate_diaoche_lane_and_cost(df, dispatch_time)
            total_dispatch_cost = df['调车费用'].sum()

            # 保存计算结果到原 CSV 文件
            def save_results_to_csv(df, file_name, output_folder):
                """
                将计算结果保存到 CSV 文件
                """
                # 重命名 Lane_Distance 列为 DIAOCHE_Lane_Distance
                df.rename(columns={'Lane_Distance': 'DIAOCHE_Lane_Distance'}, inplace=True)
                df.rename(columns={'调车费用': 'DIAOCHE_Cost'}, inplace=True)
                # 要去除的列名
                columns_to_drop = ['Truck Plate', 'SHIPMENT SOURCE COUNTRY', 'Transport Mode', 'SERVPROV NAME', 'SHIPMENT CREATED BY',
                                   'TENDERED BY', 'IS SHIPMENT CANCELLED', 'ENROUTE', 'SHIPMENT MOT', 'weight', '拼接顺序',
                                   'Location', 'Rental Unit rate', 'YYYYMM',
                                   'Rental FAIXED', 'Distance Cost', 'LANE']
                # 使用drop方法去除指定列
                df = df.drop(columns=columns_to_drop)
                # 确保保存的列包括原始列和新计算列
                columns_to_save = list(df.columns)  # 原始列
                additional_columns = ['Distance Cost(USD)', 'DIAOCHE_LANE', 'DIAOCHE_Lane_Distance', 'DIAOCHE_Cost']
                columns_to_save = [col for col in columns_to_save if
                                   col in additional_columns or col not in additional_columns]

                # 确保 "update results" 文件夹存在
                update_results_folder = os.path.join(output_folder, "update results")
                os.makedirs(update_results_folder, exist_ok=True)  # 创建文件夹，如果已存在则不会报错
                # 将结果保存到文件
                output_path = os.path.join(update_results_folder, f"{file_name}_updated.csv")
                df.to_csv(output_path, index=False, encoding='utf-8-sig')

                print(f"文件 {file_name} 的更新结果已保存至 {output_path}")

            save_results_to_csv(df, file_name, folder_path)
            df['Total Distance'] = df['DIAOCHE_Lane_Distance'] + df['SHIPMENT DISTANCE']

            # 检查必要的列
            required_columns = {'YYYYMM', 'Rental FAIXED(USD)', 'Distance Cost(USD)', 'DIAOCHE_Cost', 'Callout_Cost',
                                'SHIPMENT DISTANCE'}
            if not required_columns.issubset(df.columns):
                print(f"文件 {file_name} 缺少必要的列：{required_columns - set(df.columns)}")
                continue

            # 按月份分组，计算每月的统计值
            monthly_summary = df.groupby('YYYYMM', as_index=False).agg({
                'Rental FAIXED(USD)': 'mean',  # 每月的固定租赁费用
                'Distance Cost(USD)': 'sum',  # 每月的距离费用
                'DIAOCHE_Cost': 'sum',  # 每月的调度费用
                'Callout_Cost': 'sum',  # 每月的外叫费用
                'Total Distance': 'sum',  # 每月的运输距离
                'SHIPMENT LANE': 'count'  #每月的行程数量
            }).rename(columns={'SHIPMENT LANE': 'Trip Count'})

            # 计算每月的总租赁费用
            monthly_summary['TOTAL_RENTAL_COST'] = (
                    monthly_summary['Rental FAIXED(USD)'] +
                    monthly_summary['Distance Cost(USD)'] +
                    monthly_summary['DIAOCHE_Cost']
            ).round(2)  # 四舍五入保留整数

            # 计算每月的节省（外叫费用 - 总租赁费用）
            monthly_summary['SAVING'] = monthly_summary['Callout_Cost'] - monthly_summary['TOTAL_RENTAL_COST']
            # 计算文件的总 SAVING
            total_saving = monthly_summary['SAVING'].sum()
            # 根据总 SAVING 做出决策
            decision = 'To Rental' if total_saving > 0 else 'Keep Callout'
            # 为文件的所有行设置决策
            monthly_summary['DECISION'] = decision

            # 示例代码：将结果保存到 results 列表
            for _, row in monthly_summary.iterrows():
                truck_plate = df['Truck plate(No DG/RA)'].iloc[0]  # 假设车牌号在文件中是固定的

                results.append({
                    'File': file_name,
                    'Location': df['LOCATION'].iloc[0],  # 假设 LOCATION 在每个文件中是固定的
                    'Truck Plate': truck_plate,
                    'Month': row['YYYYMM'],
                    'Total Callout Cost': row['Callout_Cost'],
                    'Total Rental Cost': row['TOTAL_RENTAL_COST'],
                    'Saving': row['SAVING'],
                    'Total Distance': row['Total Distance'],
                    'Trip Count': row['Trip Count'],
                    'Decision': row['DECISION']
                })

            # 将结果转换为 DataFrame
            results_df = pd.DataFrame(results)
            # 初始化 Truck Identifier 列
            results_df['Truck Identifier'] = None

            # 创建一个字典，用于记录每个 Truck Plate 的文件编号
            truck_plate_file_counters = {}

            # 按 Truck Plate 和 File 分组，并生成编号
            for (truck_plate, file_name), group in results_df.groupby(['Truck Plate', 'File']):
                # 如果当前 Truck Plate 不在计数器中，初始化为 0
                if truck_plate not in truck_plate_file_counters:
                    truck_plate_file_counters[truck_plate] = 0

                # 为当前文件分配唯一编号
                truck_plate_file_counters[truck_plate] += 1

                # 为分组内的所有行分配同一个 Truck Identifier
                truck_identifier = f"{truck_plate} Truck {truck_plate_file_counters[truck_plate]}"
                results_df.loc[group.index, 'Truck Identifier'] = truck_identifier

    # 转为 DataFrame 保存结果
    results_df.to_csv(output_path, index=False, encoding='utf-8-sig')
    print(f"模拟结果已保存至: {output_path}")