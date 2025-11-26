# -*- coding: utf-8 -*-
"""
@Time ： 2025/10/20 9:29
@Auth ： Junyue Liu
@File ：mapping.py
@IDE ：PyCharm
@Motto：ABC(Always Be Coding)
"""
import pandas as pd
import numpy as np
from dateutil.relativedelta import relativedelta

def run_data_mapping(ipp_path, spend_path, month_offset):
    # 读取数据
    ipp = pd.read_excel(ipp_path, sheet_name='IPP Master Data')
    spend = pd.read_excel(spend_path, sheet_name='Sheet1')

    ipp.columns = ipp.columns.str.strip()
    spend.columns = spend.columns.str.strip()

    ipp['Saving%'] = ipp['Ann Savings'] / ipp['Ann Baseline']

    spend['PO Created Date'] = pd.to_datetime(spend['PO Created Date'])
    spend['Invoice Posting Date'] = pd.to_datetime(spend['Invoice Posting Date'])
    ipp['Implementation Start Date'] = pd.to_datetime(ipp['Implementation Start Date'], errors='coerce')

    spend['Year'] = spend['Invoice Posting Date'].dt.year

    def recurrent_period_to_months(period_str):
        if pd.isna(period_str):
            return 0
        period_str = str(period_str).strip().lower()
        if 'one-off' in period_str:
            return 12
        if 'year' in period_str:
            try:
                num = int(period_str.split('year')[0].strip())
                return num * 12
            except Exception:
                return 0
        if 'month' in period_str:
            try:
                num = int(period_str.split('month')[0].strip())
                return num
            except Exception:
                return 0
        return 0

    ipp = ipp.dropna(subset=['Recurrent period'])
    ipp['Recurrent period'] = pd.Series(ipp['Recurrent period'])
    ipp['Recurrent Months'] = ipp['Recurrent period'].apply(recurrent_period_to_months)

    def get_matching_spend(row):
        level_raw = row['Mapping level']
        if pd.isna(level_raw):
            return {2021: 0, 2022: 0, 2023: 0, 2024: 0}
        level = str(level_raw).strip()

        asl_id_raw = row['ASL ID']
        if pd.isna(asl_id_raw):
            return {2021: 0, 2022: 0, 2023: 0, 2024: 0}

        impl_start = row['Implementation Start Date']
        recurrent_months = row['Recurrent Months']
        if pd.isna(impl_start):
            return {2021: 0, 2022: 0, 2023: 0, 2024: 0}
        adjusted_date = impl_start + relativedelta(months=month_offset + recurrent_months)

        asl_ids = str(asl_id_raw).split('/')
        asl_ids = [x.strip() for x in asl_ids if x.strip()]

        spend['ASL Supplier Number'] = spend['ASL Supplier Number'].astype(str)
        spend_filtered = spend[spend['ASL Supplier Number'].isin(asl_ids)]

        mapping_detail = row['Mapping detail']

        if level == 'ASL+BPN':
            spend_filtered = spend_filtered[spend_filtered['Part Number'] == mapping_detail]
        elif level == 'ASL':
            pass
        elif level == 'ASL+Desc.':
            spend_filtered = spend_filtered[spend_filtered['Part Description'] == mapping_detail]
        elif level == 'ASL+Sub Category':
            spend_filtered = spend_filtered[spend_filtered['ASL Sub-Category'] == mapping_detail]
        elif level == 'ASL+Commodity':
            spend_filtered = spend_filtered[spend_filtered['Transaction Commodity Code'] == mapping_detail]
        else:
            return {2021: 0, 2022: 0, 2023: 0, 2024: 0}

        # 新增过滤条件：PO Created Date >= Implementation Start Date
        spend_filtered = spend_filtered[
            (spend_filtered['Invoice Posting Date'] < adjusted_date) &
            (spend_filtered['PO Created Date'] >= impl_start)
            ]

        spend_by_year = {}
        for y in [2021, 2022, 2023, 2024]:
            spend_y = spend_filtered[spend_filtered['Year'] == y]['Spend (USD)'].sum()
            spend_by_year[y] = spend_y

        return spend_by_year

    for y in [2021, 2022, 2023, 2024]:
        ipp[f'SA {y}'] = 0.0

    for idx, row in ipp.iterrows():
        spend_dict = get_matching_spend(row)
        for y in spend_dict:
            if y in [2021, 2022, 2023, 2024]:
                ipp.at[idx, f'SA {y}'] = int(round(spend_dict[y]))

    for year in [2021, 2022, 2023, 2024]:
        spend_col = f'SA {year}'
        saving_col = f'{year} SA Remap Saving'
        denom = 1 - ipp['Saving%']
        denom = denom.replace(0, np.nan)
        ipp[saving_col] = (ipp[spend_col] / denom - ipp[spend_col]).round(0)
        ipp[saving_col] = ipp[saving_col].replace([np.inf, -np.inf], np.nan).fillna(0).astype(int)
        ipp[saving_col] = ipp[saving_col].clip(lower=0)

    ipp['Saving%'] = ipp['Saving%'].fillna(0)
    ipp['Saving%'] = (ipp['Saving%'] * 100).round(0).astype(int).astype(str) + '%'

    remap_saving_cols = [f'{year} SA Remap Saving' for year in [2021, 2022, 2023, 2024]]
    ipp['IPP Saving 2021-2024'] = ipp[remap_saving_cols].sum(axis=1).astype(int)

    # 你的原有计算到remap saving部分后

    years = [2021, 2022, 2023, 2024]

    # 创建一个新的列表存储结果行，只保留需要的列
    ipp_long_rows = []

    for _, row in ipp.iterrows():
        project_name = row['Project Name'] if 'Project Name' in row else None  # 保护性判断
        for y in years:
            ipp_long_rows.append({
                'Project Name': project_name,
                'PO Year': y,
                'PO Saving': int(row.get(f'SA {y}', 0)),
                'PO Remap Saving': int(row.get(f'{y} SA Remap Saving', 0))
            })

    # 创建新的DataFrame，只包含所需的列
    ipp_long_df = pd.DataFrame(ipp_long_rows)

    # 保存结果，不带index
    output_file = 'IPP Master Data Saving.xlsx'
    ipp_long_df.to_excel(output_file, index=False)

    return output_file