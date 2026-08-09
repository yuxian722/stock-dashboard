"""
一次性診斷小工具：hourly_push.py/query_bot.py算出來的「改機完成」「改機中」
台數跟手動核對CPIS畫面對不起來時，用這支腳本把兩個資料來源的原始明細
逐筆印出來，方便直接跟CPIS的EE Maintenance Record頁面/PM Monitor頁面
一筆一筆核對，抓出差異到底在哪幾筆。

用法: python _inspect_group_changeover.py <群組>
      群組: ESEC / DB / LOC / FC(FlipChip) / EPOXY(=ESEC+DB)
範例: python _inspect_group_changeover.py DB
      python _inspect_group_changeover.py LOC
"""
import sys
import os
import sqlite3
import datetime

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import hourly_push

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "da_maintenance.db")


def _group_matches(machine_id, group):
    g = hourly_push._group_for_machine(machine_id)
    if group == "EPOXY":
        return g in ("ESEC", "DB")
    return g == group


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python _inspect_group_changeover.py <ESEC|DB|LOC|FC|EPOXY>")
        sys.exit(1)
    group = sys.argv[1].upper()
    if group == "FLIPCHIP":
        group = "FC"

    hourly_push.DB_PATH = DB_PATH
    now = datetime.datetime.now()
    shift_date, next_date = hourly_push._shift_day_bounds(now)
    print(f"現在時間: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"班別日範圍: {shift_date} 07:30 ~ {next_date} 07:30")
    print(f"查詢群組: {group}")
    print()

    # ---- 改機完成(來自EE Maintenance) ----
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT machine_id, bgn_date, bgn_time, end_date, end_time, job_code, engineer_id, dur
        FROM ee_maintenance_record
        WHERE e_tag = 'S' AND (
            (end_date = ? AND end_time >= ?)
            OR (end_date = ? AND end_time < ?)
        )
        ORDER BY machine_id, end_time
    """, (shift_date, hourly_push.SHIFT_CHANGE_TIME, next_date, hourly_push.SHIFT_CHANGE_TIME))
    all_done = cur.fetchall()
    conn.close()

    done_matched = []
    done_wrong_group = []
    done_not_real_changeover = []
    for r in all_done:
        if not _group_matches(r["machine_id"], group):
            continue
        if hourly_push._epoxy_jcode_category(r["job_code"]) is None:
            done_not_real_changeover.append(r)
            continue
        done_matched.append(r)

    print(f"【改機完成(EE Maintenance)】{group} 共 {len(done_matched)} 筆:")
    for r in done_matched:
        print(f"  {r['machine_id']}  bgn={r['bgn_date']} {r['bgn_time']}  "
              f"end={r['end_date']} {r['end_time']}  job_code={r['job_code']}  "
              f"工號={r['engineer_id']}  dur={r['dur']}")
    if done_not_real_changeover:
        print(f"  (另外有 {len(done_not_real_changeover)} 筆job_code不是CED/CEE/CD真正改機類別，不計入，如下)")
        for r in done_not_real_changeover:
            print(f"    [排除] {r['machine_id']}  end={r['end_date']} {r['end_time']}  job_code={r['job_code']}")

    # ---- 改機中/待改(來自PM Monitor) ----
    print()
    pm_rows = hourly_push.get_pm_monitor_records()
    if not pm_rows:
        print("【PM Monitor】尚未抓到資料(pm_monitor_record是空的或還沒抓過)")
    else:
        setup_rows = [r for r in pm_rows if _group_matches(r["entity"], group) and r["status"] == "SETUP"]
        wait_rows = [r for r in pm_rows if _group_matches(r["entity"], group) and r["status"] == "WAIT-SETUP"]
        print(f"【改機中(PM Monitor, STATUS=SETUP)】{group} 共 {len(setup_rows)} 筆:")
        for r in setup_rows:
            print(f"  {r['entity']}  jcode={r['jcode']}  in_time={r['in_time']}  operator={r['operator']}")
        print(f"【待改(PM Monitor, STATUS=WAIT-SETUP)】{group} 共 {len(wait_rows)} 筆:")
        for r in wait_rows:
            print(f"  {r['entity']}  jcode={r['jcode']}  in_time={r['in_time']}  operator={r['operator']}")

        # 順便列出這個群組裡PM Monitor所有其他狀態(ENG/QC/IN-REPAIR等)，
        # 避免使用者拿PM Monitor整張表逐行數、把非SETUP的狀態也算成改機中
        other_rows = [
            r for r in pm_rows
            if _group_matches(r["entity"], group) and r["status"] not in ("SETUP", "WAIT-SETUP")
        ]
        if other_rows:
            print(f"【{group}群組PM Monitor其他狀態(不算改機中/待改)】共 {len(other_rows)} 筆:")
            for r in other_rows:
                print(f"  {r['entity']}  status={r['status']}  jcode={r['jcode']}")
