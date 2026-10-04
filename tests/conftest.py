# 這兩個是「直接執行的腳本」：import 時就跑完所有檢查並 sys.exit，pytest 一收集就會
# 中斷整輪。它們要用 python 直接執行（CI 的 tests.yml 也是這樣跑），這裡讓 pytest
# 跳過，`pytest tests/` 才跑得動其餘寫成 test_ 函式的測試。
collect_ignore = ["test_keywords.py", "test_fashionjobs_blocking.py"]
