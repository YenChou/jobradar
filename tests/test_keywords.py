"""keywords.yml 的分類規則：搜尋詞、需中文／國際標籤、Customer 職類。

直接讀真正的 keywords.yml，所以改設定檔時跑一次，就知道有沒有把規則改壞。

執行：.venv/bin/python tests/test_keywords.py
"""
import pathlib
import sys
import tempfile

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from scraper import enrich, main
from scraper.main import load_cfg

cfg = load_cfg()

fail = 0
def ok(c, m):
    global fail
    print(("✅" if c else "❌") + " " + m)
    if not c: fail += 1

def job(title, desc="", **kw):
    return {"title": title, "company": kw.pop("company", "Acme"), "location": "Paris",
            "description": desc, "url": "https://example.com/" + title, "source": "Indeed", **kw}

def run(title, desc=""):
    return enrich.classify(job(title, desc), cfg)

def cats(title, desc=""):
    r = run(title, desc)
    return None if r is None else r["categories"]

def tagged(title, desc="", tag="international"):
    r = run(title, desc)
    return r is not None and tag in r["bonus_tags"]

# ── 需求 1：搜尋詞 ──
print("── 搜尋詞（英法兩種講法）──")
terms = [t.lower() for t in cfg["search_terms"]]
for t in ["crm", "chinese", "chinois", "mandarin",
          "marketing international", "international marketing", "marketing anglais", "english marketing",
          "customer marketing", "customer success", "marketing clientèle"]:
    ok(t in terms, f"搜尋詞有 {t!r}")
for t in ["international", "anglais", "english"]:
    ok(t not in terms, f"不單獨搜 {t!r}（只會掛標籤，單獨搜等於撈回所有職缺再丟掉）")
ok(terms.index("marketing automation") < terms.index("crm"),
   "核心行銷詞排在新的寬詞前面（時間預算不夠時先跳過的是寬詞）")

# ── 需求 2：明確要求中文 → 不論職類都收 ──
print()
print("── 明確要求中文的職缺：不屬於任何職類也收 ──")
r = run("Conseiller de vente (H/F)", "Maison de luxe, avenue Montaigne. Mandarin courant exigé.")
ok(r is not None and r["categories"] == [] and "chinese" in r["bonus_tags"],
   "非行銷職缺＋mandarin → 收進來、沒有職類、掛需中文")
for title, desc, why in [
    ("Assistant commercial (H/F)", "Vous parlez le chinois couramment.", "parlez le chinois"),
    ("Customer Service Representative", "Fluent Chinese and English required.", "fluent chinese（客服本來不收）"),
    ("Traducteur (H/F)", "Bilingue français-chinois.", "français-chinois"),
    ("Vendeur (H/F)", "需要中文能力，會講普通話。", "中文（CJK 字也要對得到）"),
    ("Sales Advisor", "Cantonese is a plus.", "cantonese（英文講法）"),
    ("Réceptionniste (H/F)", "Mandarin Oriental, Paris recherche un réceptionniste parlant mandarin.",
     "飯店名刪掉後仍有 parlant mandarin"),
    ("Assistant (H/F)", "Langues : français, anglais, chinois.", "Langues : …, chinois（列舉語言）"),
    ("Assistant (H/F)", "Langues parlées : français, chinois.", "Langues parlées : …"),
    ("Assistant (H/F)", "Languages: English, Chinese.", "Languages: …, Chinese"),
    ("Assistant (H/F)", "Maîtrise de l'anglais et du chinois.", "maîtrise de l'anglais et du chinois"),
    ("Vendeur (H/F)", "HSK5 requis.", "HSK 後面接數字"),
    # 第二輪 review：修誤判時刪過頭，這些明確的語言要求一度變成不收
    ("Assistant (H/F)", "Chinois obligatoire.", "Chinois obligatoire（列點開頭）"),
    ("Assistant (H/F)", "* Chinois : courant", "* Chinois : courant"),
    ("Assistant (H/F)", "- Chinois courant", "- Chinois courant（前面有空白的 - 是列點）"),
    ("Assistant (H/F)", "Vous parlez couramment chinois.", "parlez couramment chinois"),
    ("Assistant (H/F)", "Vous êtes à l'aise en chinois.", "à l'aise en chinois"),
    ("Assistant (H/F)", "Vous êtes à l’aise en chinois.", "à l’aise en chinois（彎引號）"),
    ("Assistant (H/F)", "Profil : chinois indispensable.", "子句開頭的 chinois indispensable"),
    ("Assistant (H/F)", "Chinois lu, écrit, parlé.", "Chinois lu, écrit, parlé"),
    ("Assistant (H/F)", "Le chinois est un atout.", "le chinois est un atout（冠詞＋語言名詞）"),
    ("Assistant (H/F)", "Le chinois serait un plus.", "le chinois serait un plus"),
    ("Assistant (H/F)", "La maîtrise du chinois est requise.", "du chinois est requise"),
    ("Assistant (H/F)", "Anglais et chinois courants.", "anglais et chinois courants"),
    ("Assistant (H/F)", "Vous parlez anglais et chinois couramment.", "anglais et chinois couramment"),
    ("Assistant (H/F)", "Chinois et anglais courants exigés.", "chinois et anglais courants（中文排前面）"),
    ("Assistant (H/F)", "Bilingue français et chinois.", "bilingue français et chinois"),
    ("Assistant (H/F)", "Fluent in English and Chinese.", "fluent in English and Chinese"),
    ("Assistant (H/F)", "Chinese and English required.", "Chinese and English required"),
]:
    ok(run(title, desc) is not None, f"收：{why}")

print()
print("── 只是提到中國、不代表要會中文：不收 ──")
for title, desc, why in [
    ("Cuisinier (H/F)", "Restaurant chinois recherche cuisinier.", "restaurant chinois"),
    ("Responsable logistique", "Développement sur le marché chinois.", "marché chinois"),
    ("Réceptionniste (H/F)", "Mandarin Oriental, Paris recrute un réceptionniste.", "Mandarin Oriental 是飯店"),
    ("Gestionnaire de portefeuille", "Mandarine Gestion recrute.", "Mandarine Gestion 是公司名"),
    # 以下是 PR #10 review 抓到的：講中國客戶／市場的句子，不是要求中文
    ("Vendeur (H/F)", "Nos clients chinois souhaitent un accueil premium.", "clients chinois souhaitent"),
    ("Vendeur (H/F)", "Les touristes chinois apprécient nos produits.", "touristes chinois apprécient"),
    ("Vendeur (H/F)", "Notre client chinois souhaite ouvrir une boutique.", "client chinois souhaite（單數動詞）"),
    ("Vendeur (H/F)", "Le tourisme chinois apprécie Paris.", "tourisme chinois apprécie（單數動詞）"),
    ("Responsable logistique", "Le marché chinois exige une grande réactivité.", "marché chinois exige"),
    ("Comptable", "Nos partenaires français et chinois.", "partenaires français et chinois"),
    ("Responsable logistique", "Le marché chinois lui-même.", "chinois lui（整字比對，不中 chinois lu）"),
    ("Assistant (H/F)", "Langues : français, anglais. Connaissance du marché chinois.",
     "列舉語言的句子已結束，後面的 marché chinois 不算"),
    # 把語言要求加回來後，這些仍然不能中
    ("Comptable", "Nos clients français et chinois exigeants.", "français et chinois 後面不是語言程度"),
    ("Responsable logistique", "Le visa chinois est obligatoire pour ce déplacement.",
     "visa chinois … obligatoire（chinois 不在開頭、前面不是冠詞）"),
    ("Responsable logistique", "Développement du marché chinois indispensable.", "du marché chinois ≠ du chinois"),
    ("Développeur mobile", "Native app for the Chinese market.", "native app … Chinese market"),
    # 第三輪 review：複合字裡的 - 不是列點
    ("Comptable", "Notre groupe franco-chinois souhaite renforcer son équipe.", "groupe franco-chinois souhaite"),
    ("Comptable", "Partenariat franco-chinois apprécié.", "franco-chinois apprécié"),
]:
    ok(run(title, desc) is None, f"不收：{why}")
ok(run("Stage - Assistant marketing", "Mandarin courant.") is None, "實習照樣硬性排除，要求中文也一樣")

print()
print("── 職稱裡的中文地區國旗：只有要求中文時才不算「海外市場」──")
ok(not enrich.excluded_title("Sales Associate 🇨🇳", cfg, requires_chinese=True), "要求中文：🇨🇳 放行")
ok(not enrich.excluded_title("Account Manager 🇹🇼", cfg, requires_chinese=True), "要求中文：🇹🇼 放行")
ok(enrich.excluded_title("Product Marketing Manager 🇨🇳", cfg), "沒要求中文：🇨🇳 照樣排除")
ok(enrich.excluded_title("Account Manager 🇩🇪", cfg, requires_chinese=True), "🇩🇪 一律排除")
ok(run("Sales Associate 🇨🇳", "Native Chinese speaker.") is not None, "🇨🇳 職缺＋要求中文 → 收")
ok(run("Product Marketing Manager 🇨🇳", "Launch in Shanghai, product marketing.") is None,
   "🇨🇳 行銷職缺、只是派駐中國 → 排除")

# ── 需求 2：international／anglais 等只掛標籤 ──
print()
print("── 國際（英文／國際環境／中國市場）：行銷職缺掛標籤，但不會因此入庫 ──")
for desc, why in [
    ("Anglais courant exigé.", "anglais"),
    ("Maîtrise de la langue anglaise.", "anglaise"),
    ("You will work in an international environment.", "international"),
    ("Relation avec nos clients internationaux.", "internationaux"),
    ("Équipe anglophone.", "anglophone"),
    ("Fluent English.", "english"),
    ("Lancement de la marque sur le marché chinois.", "marché chinois（中國市場算國際）"),
]:
    ok(tagged("Chargé de CRM (H/F)", desc), f"行銷職缺掛標籤：{why}")
ok(not tagged("Chargé de CRM (H/F)", "Fidélisation, emailing, newsletter."), "什麼都沒提 → 不掛")
ok(not tagged("Chargé de CRM (H/F)", "Temps forts : Noël, Saint-Valentin, Nouvel An chinois."),
   "Nouvel An chinois 只是檔期 → 不掛")
ok(not tagged("Chef de produit (H/F)", "Mandarin Oriental, Paris.", tag="chinese"),
   "Mandarin Oriental 的行銷職缺 → 不掛需中文")
ok(run("Comptable (H/F)", "Anglais courant, environnement international.") is None,
   "非行銷職缺只提到英文／國際 → 不收")
r = run("Chargé de CRM (H/F)", "Anglais courant.")
ok("international" in r["bonus_tags"] and "chinese" not in r["bonus_tags"],
   f"只提到英文 → 國際，不是需中文：{r['bonus_tags']}（需中文的篩選才能只篩出要中文的）")
ok(cfg["bonus_tags"]["chinese"]["label"] == "需中文" and cfg["bonus_tags"]["international"]["label"] == "國際",
   "標籤名稱是「需中文」「國際」")
ok(tagged("Chargé de CRM (H/F)", "Création vidéo.") is False and
   "video_content" in run("Chargé de CRM (H/F)", "Création vidéo.")["bonus_tags"],
   "影音內容標籤照舊")

# ── 需求 3：Customer 職類 ──
print()
print("── Customer 職類 ──")
for title in ["Customer Marketing Specialist", "Customer Success Manager (H/F)",
              "Chargé(e) Expérience Client", "Responsable Marketing Clientèle",
              "Customer Lifecycle Manager", "Responsable Engagement Client Omnicanal"]:
    c = cats(title)
    ok(c is not None and "customer" in c, f"{title} → {c}")
for title in ["Customer Experience - centre d’appels", "Customer Experience - centre d'appels",
              "Customer Service Representative", "Conseiller relation client (H/F)",
              "Conseiller Expérience Client (H/F)", "Customer Experience Agent",
              "Téléconseiller Customer Success (H/F)"]:
    ok(cats(title) is None, f"客服職缺不收：{title}")
c = cats("Chargé de marketing produit", "Améliorer l'expérience client et la customer experience.")
ok(c is not None and "customer" not in c and "marketing_produit" in c,
   f"Customer 只看職稱：描述提到 expérience client 不會掛上 Customer → {c}")
r = run("Customer Success Manager", "Réduire le churn, suivre le NPS.")
ok({"churn", "nps"} <= set(r["skills"]), f"Customer 的技能關鍵字加分：{r['skills']}")
ok(cfg["categories"]["customer"]["label"] == "Customer", "職類名稱是 Customer")

# ── 英文實習（intern）要排除，但 international、interne 不能被誤殺 ──
print()
print("── intern 整字排除 ──")
for t in ["Marketing Specialist Intern", "Intern, Consultant Cyber Threat Intelligence",
          "Business Development Intern (Mandarin Speaker)", "Marketing Manager - Intern",
          "Product Marketing Interns 2027"]:
    ok(enrich.excluded_title(t, cfg), f"排除：{t}")
ok(run("Business Development Intern (Mandarin Speaker)", "Mandarin native.") is None,
   "要求中文的實習也排除（實習一律不收）")
for t in ["International Marketing Manager", "Chef de produit international junior",
          "Chargé de communication interne (H/F)", "Internal Communications Specialist"]:
    ok(not enrich.excluded_title(t, cfg), f"不誤殺：{t}")

# ── 彎引號 ’ 與直引號 ' 要一樣 ──
print()
print("── 彎引號與直引號視為相同 ──")
from scraper.util import norm
ok(norm("Centre d’appels") == norm("centre d'appels") == "centre d'appels", "norm 把 ’ 統一成 '")
c = cats("Chargée d’acquisition (H/F)")
ok(c is not None and "performance_marketing" in c, f"Chargée d’acquisition（彎引號）→ {c}")

# ── 設定檔裡寫大寫、重音也要對得到 ──
print()
print("── keep_keywords／ignore_phrases 也會正規化 ──")
raw = yaml.safe_load((main.ROOT / "keywords.yml").read_text(encoding="utf-8"))
raw["bonus_tags"]["chinese"]["keep_keywords"].append("Chinois Apprécié Exprès")
raw["bonus_tags"]["chinese"]["ignore_phrases"].append("Hôtel Pékin")
raw["bonus_tags"]["video_content"]["ignore_phrases"] = None   # 清單項目全被註解掉的樣子
with tempfile.TemporaryDirectory() as tmp:
    (pathlib.Path(tmp) / "keywords.yml").write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    real_root, main.ROOT = main.ROOT, pathlib.Path(tmp)
    try:
        tags = load_cfg()["bonus_tags"]
    finally:
        main.ROOT = real_root
ok("chinois apprecie expres" in tags["chinese"]["keep_keywords"], "keep_keywords 去重音、轉小寫")
ok("hotel pekin" in tags["chinese"]["ignore_phrases"], "ignore_phrases 去重音、轉小寫")
ok(tags["video_content"]["ignore_phrases"] == [], "清單全被註解掉（YAML 給 None）不會崩潰")

# ── 去重：標籤取聯集 ──
print()
print("── 去重時標籤取聯集 ──")
a = enrich.classify(job("Chargé de CRM (H/F)", "", source="Isarta"), cfg)  # Isarta 沒有描述
b = enrich.classify(job("Chargé de CRM (H/F)", "Mandarin courant."), cfg)
merged = enrich.dedupe([a, b])
ok(len(merged) == 1 and merged[0]["bonus_tags"] == ["chinese"],
   f"沒描述的那筆排前面，合併後仍保有標籤 → {merged[0]['bonus_tags']}")

print()
print("全部通過 ✅" if not fail else f"{fail} 項失敗 ❌")
sys.exit(1 if fail else 0)
