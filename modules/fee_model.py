import pandas as pd
import numpy as np

def calculate_rental_cost(rental_info_path, rental_truck_list_path, exchange_rate_path, output_path):
    """
    月租车费用计算模块
    """
    # 读取数据
    rental_info = pd.read_csv(rental_info_path)
    rental_truck_list = pd.read_csv(rental_truck_list_path)
    exchange_rate = pd.read_excel(exchange_rate_path)
    # special_lane_df 已经是字符串了，确认exchange_rate也转成字符串：
    exchange_rate['YYYYMM'] = pd.to_datetime(exchange_rate['YYYYMM'], errors='coerce').dt.strftime('%Y/%m')
    # 提取车型（如 15T）并合并到 grouped
    rental_truck_list['车型'] = rental_truck_list['Vehicle Type'].str.extract(r'(\d+T)')
    # 合并租赁信息和租赁车辆列表，使用内连接保证匹配
    merged_rental_df = pd.merge(
        rental_info,
        rental_truck_list,
        on='Truck Plate',
        how='inner'
    )
    merged_rental_df.columns = merged_rental_df.columns.str.strip()

    # 让 LOCATION 列的值等于 Location 列的值
    merged_rental_df['LOCATION'] = merged_rental_df['Location']
    # LOCATION 转大写
    merged_rental_df['LOCATION'] = merged_rental_df['LOCATION'].str.upper()

    # 清洗 Fix rental Cost(CNY without VAT) 列
    merged_rental_df['Fix rental Cost(CNY without VAT)'] = (
        merged_rental_df['Fix rental Cost(CNY without VAT)']
        .astype(str)
        .str.replace('[^\d.]', '', regex=True)
        .replace('', pd.NA)
        .astype('float')
    )

    # 提取年月列 YYYY/MM
    merged_rental_df['YYYYMM'] = pd.to_datetime(
        merged_rental_df['SHIPMENT CREATION DATE'], errors='coerce'
    ).dt.strftime('%Y/%m')

    # 根据 INVOICE TYPE 判断使用哪一列进行计算 Variable Cost (USD)
    def calculate_variable_cost(row):
        if row['INVOICE TYPE'] == 'PARENT':
            return row['SHIPMENT ACCESSORIAL COST USD']  # 如果是 PARENT，使用 SHIPMENT ACCESSORIAL COST USD
        else:
            return row['TOTAL INVOICE COST (USD)']  # 如果不是 PARENT，使用 TOTAL INVOICE COST (USD)

    # 逐行计算每一行的 Variable Cost (USD)
    merged_rental_df['Variable Cost (USD)'] = merged_rental_df.apply(calculate_variable_cost, axis=1)

    # 按 Truck Plate、LOCATION、YYYYMM 汇总费用
    monthly_cost_df = merged_rental_df.groupby(
        ['Truck Plate', 'LOCATION', 'YYYYMM', '车型'], as_index=False
    ).agg({
        'SHIPMENT ACCESSORIAL COST USD': 'sum',  # 汇总附加费用
        'Fix rental Cost(CNY without VAT)': 'first',  # 固定月租费取首条
        'Variable Cost (USD)': 'sum',  # 汇总计算后的 Variable Cost
        'TOTAL INVOICE COST (USD)': 'sum'  # 汇总 TOTAL INVOICE COST (USD)
    })
    print(monthly_cost_df.columns.tolist())
    # 合并汇率，匹配每月汇率
    monthly_cost_df = pd.merge(
        monthly_cost_df,
        exchange_rate,
        on='YYYYMM',
        how='left'
    )

    # 计算美元月租费用
    monthly_cost_df['Fix rental Cost(USD)'] = (
            monthly_cost_df['Fix rental Cost(CNY without VAT)'] / monthly_cost_df['Dollar_RMB_rate']
    )

    # 计算 Total Rental Cost
    monthly_cost_df['Total Rental Cost'] = (
            monthly_cost_df['Fix rental Cost(USD)'] + monthly_cost_df['Variable Cost (USD)']
    )

    # 四舍五入
    for col in ['Fix rental Cost(USD)', 'Total Rental Cost']:
        monthly_cost_df[col] = monthly_cost_df[col].round(2)

    # 保存结果
    monthly_cost_df.to_csv(output_path, index=False)

    return monthly_cost_df

def calculate_callout_cost(callout_info_path, callout_cost_path, rental_truck_list_path, exchange_rate_path, output_path):
    """
    外叫车费用计算模块，增加美元换算
    """
    callout_info = pd.read_csv(callout_info_path)
    callout_cost = pd.read_csv(callout_cost_path)
    rental_truck_list = pd.read_csv(rental_truck_list_path)
    exchange_rate = pd.read_excel(exchange_rate_path)
    # special_lane_df 已经是字符串了，确认exchange_rate也转成字符串：
    exchange_rate['YYYYMM'] = pd.to_datetime(exchange_rate['YYYYMM'], errors='coerce').dt.strftime('%Y/%m')
    # 筛选 Special_Lane 为 'N'
    callout_info_filtered = callout_info[callout_info['Special_Lane'] == 'N']

    # 基地列大写
    callout_cost['基地'] = callout_cost['基地'].str.upper()

    # 合并 callout_info 和租赁车辆列表
    merged_callout_df = pd.merge(
        callout_info_filtered,
        rental_truck_list,
        on='Truck Plate',
        how='left'
    )

    # 提取车型（如 15T）
    merged_callout_df['车型'] = merged_callout_df['Vehicle Type'].str.extract(r'(\d+T)')

    # 基于车型和基地匹配 callout_cost
    merged_callout_df = pd.merge(
        merged_callout_df,
        callout_cost,
        left_on=['车型', 'LOCATION'],
        right_on=['车型', '基地'],
        how='left'
    )

    # 时间列转 datetime
    time_columns = [
        'SHIPMENT GATE OUT SOURCE',
        'SHIPMENT GATE IN SOURCE',
        'SHIPMENT GATE IN DESTINATION',
        'SHIPMENT GATE OUT DESTINATION'
    ]
    for col in time_columns:
        if col in merged_callout_df.columns:
            merged_callout_df[col] = pd.to_datetime(merged_callout_df[col], errors='coerce')

    # 提取年月列 YYYY/MM，用于匹配汇率
    merged_callout_df['YYYYMM'] = pd.to_datetime(
        merged_callout_df['SHIPMENT CREATION DATE'], errors='coerce'
    ).dt.strftime('%Y/%m')

    # 计算里程费用
    merged_callout_df['Mile Cost($)'] = merged_callout_df['每公里/车型'] * merged_callout_df['SHIPMENT DISTANCE']

    # 过夜费用计算：计算天数，最少1天
    merged_callout_df['Source Overnights'] = (
        (merged_callout_df['SHIPMENT GATE OUT SOURCE'].dt.normalize() - merged_callout_df[
            'SHIPMENT GATE IN SOURCE'].dt.normalize()).dt.days
    )
    merged_callout_df['Source Overnights'] = np.maximum(merged_callout_df['Source Overnights'], 0)
    merged_callout_df['Destination Overnights'] = (
        (merged_callout_df['SHIPMENT GATE OUT DESTINATION'].dt.normalize() - merged_callout_df[
            'SHIPMENT GATE IN DESTINATION'].dt.normalize()).dt.days
    )
    merged_callout_df['Destination Overnights'] = np.maximum(merged_callout_df['Destination Overnights'], 0)
    merged_callout_df['Total Overnights'] = merged_callout_df['Source Overnights'] + merged_callout_df[
        'Destination Overnights']

    merged_callout_df['Stand_by Cost(￥)'] = merged_callout_df['Total Overnights'] * merged_callout_df['费用/每天']

    # 合并汇率数据，根据 YYYYMM 匹配
    merged_callout_df = pd.merge(
        merged_callout_df,
        exchange_rate[['YYYYMM', 'Dollar_RMB_rate']],
        on='YYYYMM',
        how='left'
    )

    # 计算人民币费用总和
    merged_callout_df['Total Cost($)'] = merged_callout_df['Mile Cost($)'].fillna(0)

    # 换算为美元
    # merged_callout_df['Mile Cost($)'] = merged_callout_df['Mile Cost(￥)'] / merged_callout_df['Dollar_RMB_rate']
    # merged_callout_df['Stand_by Cost($)'] = merged_callout_df['Stand_by Cost(￥)'] / merged_callout_df['Dollar_RMB_rate']
    # merged_callout_df['Total Cost($)'] = merged_callout_df['Total Cost(￥)'] / merged_callout_df['Dollar_RMB_rate']

    # 四舍五入并转换类型（支持缺失）
    for col in [
        '费用/每天', 'Total Cost($)'
    ]:
        if col in merged_callout_df.columns:
            merged_callout_df[col] = merged_callout_df[col].round(2)
    # 删除人民币费用相关列
    if 'EQUIPMENT ID_x' in merged_callout_df.columns and 'EQUIPMENT ID_y' in merged_callout_df.columns:
        # 以 EQUIPMENT ID_x 为准，删除 EQUIPMENT ID_y
        merged_callout_df = merged_callout_df.rename(columns={'EQUIPMENT ID_x': 'EQUIPMENT ID'})
        merged_callout_df = merged_callout_df.drop(columns=['EQUIPMENT ID_y'])
    elif 'EQUIPMENT ID_x' in merged_callout_df.columns:
        merged_callout_df = merged_callout_df.rename(columns={'EQUIPMENT ID_x': 'EQUIPMENT ID'})
    elif 'EQUIPMENT ID_y' in merged_callout_df.columns:
        merged_callout_df = merged_callout_df.rename(columns={'EQUIPMENT ID_y': 'EQUIPMENT ID'})
    cols_to_drop = ['Source Overnights', 'Destination Overnights', 'Total Overnights', 'Stand_by Cost(￥)', 'Dollar_RMB_rate', '费用/每天']
    merged_callout_df.drop(columns=[col for col in cols_to_drop if col in merged_callout_df.columns], inplace=True)

    # 保存结果
    merged_callout_df.to_csv(output_path, index=False, encoding='utf-8-sig')

    return merged_callout_df


def calculate_special_lane_cost(callout_info_path, special_lane_cost_path, rental_truck_list_path, exchange_rate_path, output_path):
    """
    特殊路线费用计算模块，增加美元换算
    """
    callout_info = pd.read_csv(callout_info_path)
    special_lane_cost = pd.read_csv(special_lane_cost_path, index_col=0)
    rental_truck_list = pd.read_csv(rental_truck_list_path)
    exchange_rate = pd.read_excel(exchange_rate_path)
    # special_lane_df 已经是字符串了，确认exchange_rate也转成字符串：
    exchange_rate['YYYYMM'] = pd.to_datetime(exchange_rate['YYYYMM'], errors='coerce').dt.strftime('%Y/%m')
    # 筛选 Special_Lane 为 'Y'
    special_lane_df = callout_info[callout_info['Special_Lane'] == 'Y']

    # 合并车型信息
    special_lane_df = pd.merge(
        special_lane_df,
        rental_truck_list,
        left_on='Truck Plate',
        right_on='Truck Plate',
        how='left'
    )

    # 提取车型，如 20T
    special_lane_df['车型'] = special_lane_df['Vehicle Type'].str.extract(r'(\d+T)')

    # 检查未提取车型的记录
    missing_vehicle_rows = special_lane_df[special_lane_df['车型'].isna()]
    if not missing_vehicle_rows.empty:
        print("以下记录未提取到车型信息，请检查Rental truck list表的 EQUIPMENT ID是否缺漏：")
        print(missing_vehicle_rows[['Truck Plate', 'SHIPMENT LANE']])

    # 匹配特殊路线费用（人民币）
    def get_special_lane_fee(row):
        try:
            return special_lane_cost.loc[row['车型'], row['SHIPMENT LANE']]
        except KeyError:
            return np.nan

    special_lane_df['Special_lane Cost(￥)'] = special_lane_df.apply(get_special_lane_fee, axis=1)

    # 提取年月列 YYYY/MM
    special_lane_df['YYYYMM'] = pd.to_datetime(
        special_lane_df['SHIPMENT CREATION DATE'], errors='coerce'
    ).dt.strftime('%Y/%m')

    # 合并汇率，转换为美元
    special_lane_df = pd.merge(
        special_lane_df,
        exchange_rate[['YYYYMM', 'Dollar_RMB_rate']],
        on='YYYYMM',
        how='left'
    )

    special_lane_df['Special_lane Cost($)'] = special_lane_df['Special_lane Cost(￥)'] / special_lane_df['Dollar_RMB_rate']

    # 检查未匹配费用记录
    unmatched_rows = special_lane_df[special_lane_df['Special_lane Cost(￥)'].isna()]
    if not unmatched_rows.empty:
        print("以下记录未匹配到特殊路线费用，请检查special_lane cost表里的 SHIPMENT LANE 和 车型：")
        print(unmatched_rows[['Truck Plate', 'SHIPMENT LANE', '车型']])

    # 四舍五入
    special_lane_df['Special_lane Cost(￥)'] = special_lane_df['Special_lane Cost(￥)'].round(2)
    special_lane_df['Special_lane Cost($)'] = special_lane_df['Special_lane Cost($)'].round(2)

    if 'Special_lane Cost(￥)' in special_lane_df.columns:
        special_lane_df.drop(columns=['Special_lane Cost(￥)'], inplace=True)
    if 'EQUIPMENT ID_x' in special_lane_df.columns and 'EQUIPMENT ID_y' in special_lane_df.columns:
        # 以 EQUIPMENT ID_x 为准，删除 EQUIPMENT ID_y
        special_lane_df = special_lane_df.rename(columns={'EQUIPMENT ID_x': 'EQUIPMENT ID'})
        special_lane_df = special_lane_df.drop(columns=['EQUIPMENT ID_y'])
    elif 'EQUIPMENT ID_x' in special_lane_df.columns:
        special_lane_df = special_lane_df.rename(columns={'EQUIPMENT ID_x': 'EQUIPMENT ID'})
    elif 'EQUIPMENT ID_y' in special_lane_df.columns:
        special_lane_df = special_lane_df.rename(columns={'EQUIPMENT ID_y': 'EQUIPMENT ID'})
    # 保存结果
    special_lane_df.to_csv(output_path, index=False, encoding='utf-8-sig')

    return special_lane_df
