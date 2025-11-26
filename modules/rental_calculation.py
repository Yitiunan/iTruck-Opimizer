import pandas as pd
import numpy as np

def calculate_rental_time_utilization(rental_info_path, output_path, rental_cost_path, callout_cost_path, rental_truck_list_path, exchange_rate_path, rental_to_callout_compare_path):
    """
    计算月租车时间利用率并保存结果
    """
    rental_info = pd.read_csv(rental_info_path)
    rental_truck_list = pd.read_csv(rental_truck_list_path)
    rental_cost = pd.read_csv(rental_cost_path)
    callout_cost = pd.read_csv(callout_cost_path)

    # 转换时间列为 datetime
    time_columns = ['SHIPMENT GATE OUT SOURCE', 'SHIPMENT GATE IN SOURCE',
                    'SHIPMENT GATE IN DESTINATION', 'SHIPMENT GATE OUT DESTINATION', 'SHIPMENT CREATION DATE']
    for col in time_columns:
        rental_info[col] = pd.to_datetime(rental_info[col], errors='coerce')
    rental_info['LOCATION'] = rental_info['LOCATION'].str.upper()

    # 计算时间间隔，以天为单位
    def calculate_hours(delta):
        days = delta.days  # 获取完整天数
        remaining_seconds = delta.total_seconds() - days * 24 * 3600  # 剩余不足一天的秒数
        remaining_hours = remaining_seconds / 3600  # 转换为小时

        # 如果剩余小时超过 10 小时，按 10 小时计算；否则按实际小时数计算
        additional_hours = min(remaining_hours, 10)

        # 每天按 10 小时计算总小时数
        total_hours = days * 10 + additional_hours
        return total_hours

    # 应用自定义函数计算时间间隔（小时）
    time_deltas = rental_info['SHIPMENT GATE OUT DESTINATION'] - rental_info['SHIPMENT GATE IN SOURCE']
    rental_info['时间间隔（小时）'] = time_deltas.apply(calculate_hours)

    # 提取所属月份
    rental_info['所属月份'] = rental_info['SHIPMENT CREATION DATE'].dt.strftime('%Y-%m')

    # 一个月按30天，每天10小时计算总秒数
    seconds_per_month = float(30 * 10)

    # 分组统计
    grouped = rental_info.groupby(['Truck Plate', 'LOCATION', '所属月份']).agg({
        '时间间隔（小时）': 'sum',
        'SHIPMENT DISTANCE': 'sum'
    }).reset_index()

    # 计算时间利用率
    grouped['时间利用率'] = (grouped['时间间隔（小时）'] / seconds_per_month).round(2)

    # 提取车型（如 15T）并合并到 grouped
    rental_truck_list['车型'] = rental_truck_list['Vehicle Type'].str.extract(r'(\d+T)')
    grouped = pd.merge(
        grouped,
        rental_truck_list[['Truck Plate', '车型']],
        on='Truck Plate',
        how='left'
    )

    # 用 grouped 的 LOCATION 和 车型 匹配 rental_cost_path 的 Location 和 Truck plate(No DG/RA)
    grouped = pd.merge(
        grouped,
        rental_cost[['Location', 'Truck plate(No DG/RA)', 'Rental FAIXED', 'Rental Unit rate']],
        left_on=['LOCATION', '车型'],
        right_on=['Location', 'Truck plate(No DG/RA)'],
        how='left'
    )
    # 基地列大写
    callout_cost['基地'] = callout_cost['基地'].str.upper()
    # 用 grouped 的 LOCATION 和 车型 匹配 callout_cost_path 的 基地 和 车型
    grouped = pd.merge(
        grouped,
        callout_cost[['基地', '车型', '每公里/车型']],
        left_on=['LOCATION', '车型'],
        right_on=['基地', '车型'],
        how='left'
    )
    # 确保两列格式一致
    grouped['所属月份'] = grouped['所属月份'].str.replace('-', '/')
    # 用 grouped 的 所属月份 列与 exchange_rate_path 的 YYYYMM 列匹配，获取汇率
    exchange_rate = pd.read_excel(exchange_rate_path)
    # special_lane_df 已经是字符串了，确认exchange_rate也转成字符串：
    exchange_rate['YYYYMM'] = pd.to_datetime(exchange_rate['YYYYMM'], errors='coerce').dt.strftime('%Y/%m')
    grouped = pd.merge(
        grouped,
        exchange_rate[['YYYYMM', 'Dollar_RMB_rate']],
        left_on='所属月份',
        right_on='YYYYMM',
        how='left'
    )

    # 将 'Rental FAIXED' 和 'Rental Unit rate' 转换为美元
    grouped['Rental FAIXED(USD)'] = grouped['Rental FAIXED'] / grouped['Dollar_RMB_rate']
    grouped['Rental Unit rate(USD)'] = grouped['Rental Unit rate'] / grouped['Dollar_RMB_rate']

    # 计算每行的 Break Even Point (x)
    # 根据公式：Rental FAIXED(USD) + (Rental Unit rate(USD) × x) = x × 每公里/车型
    grouped['Break Even Point'] = grouped.apply(
        lambda row: row['Rental FAIXED(USD)'] / (row['每公里/车型'] - row['Rental Unit rate(USD)'])
        if row['每公里/车型'] > row['Rental Unit rate(USD)'] else np.nan,
        axis=1
    ).round().astype('Int64')

    # 将 ‘所属月份’ 转换为 YYYYMM 格式，（去掉-和/）
    grouped['YYYYMM'] = grouped['所属月份']

    # 选择输出列，替换所属月份为YYYYMM
    columns_to_keep = [
        'Truck Plate', 'LOCATION', 'YYYYMM', '时间间隔（小时）', 'SHIPMENT DISTANCE',
        '时间利用率', '车型', '每公里/车型', 'Rental FAIXED(USD)', 'Rental Unit rate(USD)', 'Break Even Point'
    ]
    grouped = grouped[columns_to_keep]

    # 保存月租车时间利用率结果
    grouped.to_csv(output_path, index=False, encoding='utf-8-sig')
    print(f"月租车时间利用率结果已保存至: {output_path}")

    # --- 新增合并步骤 ---
    # 读取rental_to_callout_compare.csv
    rental_to_callout_compare_df = pd.read_csv(rental_to_callout_compare_path)

    # 合并两个DataFrame，左表为rental_to_callout_compare_df，按Truck Plate、LOCATION、YYYYMM匹配
    merged_df = pd.merge(
        rental_to_callout_compare_df,
        grouped,
        on=['Truck Plate', 'LOCATION', 'YYYYMM'],
        how='left',
        suffixes=('', '_util')
    )

    # 删除多余列：grouped中的 'Truck Plate', 'LOCATION' (带后缀的) 和 '所属月份' (已在grouped里去掉)
    cols_to_drop = [col for col in merged_df.columns if col.endswith('_util')]
    merged_df.drop(columns=cols_to_drop, inplace=True)

    # 将合并结果覆盖写回rental_to_callout_compare.csv
    merged_df.to_csv(rental_to_callout_compare_path, index=False, encoding='utf-8-sig')
    print(f"合并后的结果已覆盖保存至: {rental_to_callout_compare_path}")