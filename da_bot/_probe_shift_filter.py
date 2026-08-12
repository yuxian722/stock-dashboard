"""
一次性診斷小工具：驗證cpis_api.fetch_ee_maintenance_xls()帶shift=AD/AN/BD/BN
查詢CPIS時，伺服器端到底有沒有真的依照這個參數過濾資料(2026/08/12使用者
實測反應：查"2100 AD改機"，報表裡卻同時出現早班13台+夜班16台=29台，
懷疑shift這個查詢參數根本沒被CPIS報表產生端點(maintenance_record_r.aspx)
實際套用，只是靜默忽略、還是回傳整天(全部班別)的資料)。

這支腳本必須在有真正CPIS連線(config.txt填好apg_user/apg_password)的機器
上執行才有意義(這個環境沒有CPIS連線，沒辦法自己驗證)。

用法: python _probe_shift_filter.py <YYYYMMDD> [entity]
範例: python _probe_shift_filter.py 20260811
      python _probe_shift_filter.py 20260811 BA2*

會依序用shift=None/AD/AN/BD/BN各查一次同一天同一個entity的資料，印出
每次抓到的「改機完成(e_tag=S)」筆數，方便直接判斷：
  - 如果AD/AN/BD/BN四次筆數加起來 == None那次的筆數，而且互不重疊，
    代表shift過濾是真的有作用，四班合起來剛好等於不篩選的全部。
  - 如果AD/AN/BD/BN每次筆數都跟None那次幾乎一樣(沒有明顯縮小)，
    代表shift這個查詢參數被CPIS忽略掉了，過濾根本沒發生，
    需要改用別的方式(例如查maintenance_record_h.aspx那個有dropdown的
    表單頁面、模擬真正的POST postback)才能真正依班別篩選。
"""
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import cpis_api
import cpis_scraper

SHIFTS = ["None", "AD", "AN", "BD", "BN"]


def _fetch_machine_ids(date_ymd, entity, shift):
    xls_chunks = cpis_api.fetch_ee_maintenance_xls(date_ymd, date_ymd, entity=entity, shift=shift)
    records = []
    for raw in xls_chunks:
        records.extend(cpis_scraper.parse_ee_maintenance_xls(raw))
    changeover_done = [r for r in records if r.get("e_tag") == "S"]
    return changeover_done


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python _probe_shift_filter.py <YYYYMMDD> [entity，預設BA*]")
        sys.exit(1)
    date_ymd = sys.argv[1]
    entity = sys.argv[2] if len(sys.argv) > 2 else "BA*"

    print(f"查詢日期: {date_ymd}  entity: {entity}")
    print("=" * 60)

    results = {}
    for shift in SHIFTS:
        try:
            rows = _fetch_machine_ids(date_ymd, entity, shift)
        except Exception as e:
            print(f"shift={shift:<5} 查詢失敗: {type(e).__name__}: {e}")
            continue
        results[shift] = rows
        machine_end_pairs = {(r.get("machine_id"), r.get("end_time")) for r in rows}
        print(f"shift={shift:<5} 改機完成(e_tag=S)筆數: {len(rows)}  (不重複機台+結束時間組合: {len(machine_end_pairs)})")

    print("=" * 60)
    if "None" in results:
        none_count = len(results["None"])
        ad_an_bd_bn_total = sum(len(results[s]) for s in ("AD", "AN", "BD", "BN") if s in results)
        print(f"shift=None(不篩班別)筆數: {none_count}")
        print(f"AD+AN+BD+BN四班加總筆數: {ad_an_bd_bn_total}")
        if ad_an_bd_bn_total == 0:
            print("=> 四個班別查詢都是0筆，可能是這天真的沒資料，或shift參數格式不對(伺服器直接查不到)，建議換一天有資料的日期重測")
        elif abs(ad_an_bd_bn_total - none_count) <= max(1, none_count * 0.05):
            print("=> 四班加總跟不篩選幾乎一致，shift過濾看起來確實有作用")
        else:
            print("=> 四班加總跟不篩選對不起來，shift參數可能沒有被CPIS正確套用，需要進一步確認")

        # 檢查AD/AN/BD/BN彼此之間有沒有明顯重疊(同一筆(機台,結束時間)同時出現在兩個班別)
        pair_sets = {
            s: {(r.get("machine_id"), r.get("end_time")) for r in results[s]}
            for s in ("AD", "AN", "BD", "BN") if s in results
        }
        shifts_present = list(pair_sets.keys())
        for i in range(len(shifts_present)):
            for j in range(i + 1, len(shifts_present)):
                s1, s2 = shifts_present[i], shifts_present[j]
                overlap = pair_sets[s1] & pair_sets[s2]
                if overlap:
                    print(f"=> [警告] shift={s1} 跟 shift={s2} 之間有 {len(overlap)} 筆重複的(機台,結束時間)組合，"
                          f"代表這兩次查詢回傳了同一批資料，shift過濾很可能沒有真的生效")
                    for pair in list(overlap)[:5]:
                        print(f"     重複範例: 機台={pair[0]}  結束時間={pair[1]}")
