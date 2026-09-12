# -*- coding: utf-8 -*-
"""فحصُ مِسبار الدفعة — على وحدةٍ وهميّةٍ تحاكي بنية quantum_matrix_v2.

يُشغَّل بأمرٍ واحدٍ ولا يمسّ البوتَ ولا الشبكةَ ولا أيَّ ملفّ:

    python tests/test_scan_probe.py

كلُّ فحصٍ أدناه **أسقطَ النسخةَ الأصليّة** إلّا حيث يُقال خلافُ ذلك،
وموضعُ العطل مكتوبٌ في `docs/PROBE_AUDIT_2026-09-12.md`.
"""
import concurrent.futures as cf
import os
import sys
import threading
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scan_probe                                           # noqa: E402

FAILED = []
PASSED = []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("   %s %s%s" % ("✔" if cond else "✗", name,
                          ("  ⇐ " + detail) if detail and not cond else ""))


# ── الوحدةُ الوهميّة ────────────────────────────────────────────────────

class FakeExchange:
    """كائنُ بورصةٍ مزيَّف — بقفلٍ يُسلسِل النداءات كمنظّم معدّل ccxt."""
    rateLimit = 20
    enableRateLimit = True

    def __init__(self, delay=0.0):
        self.delay = delay
        self.calls = []
        self._gate = threading.Lock()

    def fetch_ohlcv(self, symbol, timeframe="1h", limit=100):
        with self._gate:
            if self.delay:
                time.sleep(self.delay)
            self.calls.append((symbol, timeframe, limit))
        return [[0, 1.0, 2.0, 0.5, 1.5, 10.0]] * min(limit, 5)

    def fetch_positions(self, **kw):
        if self.delay:
            time.sleep(self.delay)
        return []


class _BaseScanner:
    def __init__(self, ex, plan):
        self.ex = ex
        self.plan = plan

    def _scan_one(self, ticker):
        for _ in range(self.plan.get(ticker, 12)):
            self.ex.fetch_ohlcv(ticker, "1h", 200)
        return {"ticker": ticker}


def build(plan, delay=0.0, n_positions=0, own_scan=False, complete=True):
    """وحدةٌ وهميّةٌ كاملة. `own_scan` يضع _scan_one على الصنف نفسِه."""
    mod = types.ModuleType("fakebot")
    ex = FakeExchange(delay)
    mod.exchange = ex
    body = {"_scan_one": _BaseScanner._scan_one} if own_scan else {}
    mod.TechnicalScanner = type("TechnicalScanner", (_BaseScanner,), body)

    def check_position_reversals():
        ex.fetch_positions(settleCoin="USDT")
        for _ in range(n_positions):
            ex.fetch_ohlcv("POS/USDT", "4h", 260)
        return n_positions

    mod.check_position_reversals = check_position_reversals

    class _State:
        def log_activity(self, m):
            pass

    mod.STATE = _State()
    mod.plan = plan
    if not complete:
        del mod.TechnicalScanner
    return mod


def batch(mod, tickers, workers=1):
    sc = mod.TechnicalScanner(mod.exchange, mod.plan)
    if workers == 1:
        for t in tickers:
            sc._scan_one(t)
    else:
        with cf.ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(sc._scan_one, tickers))
    mod.check_position_reversals()


def ten():
    """عشرُ عملات: خمسٌ تخرج مبكّراً بنداءين · وخمسٌ مسحاً كاملاً باثنَي عشر."""
    plan, tickers = {}, []
    for i in range(10):
        t = "C%02d/USDT" % i
        tickers.append(t)
        plan[t] = 2 if i < 5 else 12
    return plan, tickers


def field(lines, needle):
    for L in lines:
        if needle in L:
            return L
    return ""


# ── ① الوسمُ البارد ورقمُ الدفعة ─────────────────────────────────────────
print("\n① الوسمُ البارد ورقمُ الدفعة — تُحسَم فرضيّةُ «الخبائثُ باردة»")
plan, tickers = ten()
mod = build(plan, n_positions=3)
lines = []
scan_probe.install(mod, log=lines.append)
batch(mod, tickers)
batch(mod, tickers)
heads = [L for L in lines if "إجمالاً" in L]
check("سطرٌ واحدٌ لكلّ دفعة", len(heads) == 2, "عدد=%d" % len(heads))
check("الأولى موسومةٌ [باردة]", "باردة" in heads[0])
check("الثانية غيرُ موسومة", "باردة" not in heads[1])
check("ترقيمُ الدفعات #1 ثمّ #2",
      "#1" in heads[0] and "#2" in heads[1], heads[1][:40])
scan_probe.uninstall(mod)

# ── ② فرزُ العملات وعدمُ ازدواج نداءات المشتبَه الثاني ──────────────────
print("\n② الفرزُ وعدمُ الازدواج — بخيطٍ واحد")
mod = build(plan, n_positions=3)
lines = []
scan_probe.install(mod, log=lines.append)
batch(mod, tickers)
srt = field(lines, "خرجت مبكّراً")
net = field(lines, "ومنه شبكة")
check("خمسٌ مبكّرة وخمسٌ كاملة",
      "(≤2 نداء) 5" in srt and "(≥10) 5" in srt, srt)
check("نداءاتُ السكانر 70 بلا نداءات الانعكاس",
      "(70 نداء)" in net, net)
check("نداءاتُ الكائن الفعليّة 73 (70 + 1 مركز + ... )",
      len(mod.exchange.calls) == 70 + 3, str(len(mod.exchange.calls)))
scan_probe.uninstall(mod)

# ── ③ خيطٌ منافسٌ حقيقيّ ─────────────────────────────────────────────────
print("\n③ خيطٌ منافسٌ يمسّ نفسَ الكائن — يُرصَد ويُفصَل")
mod = build(plan, delay=0.001)
lines = []
scan_probe.install(mod, log=lines.append)
stop = threading.Event()
n_rival = [0]


def rival():
    while not stop.is_set():
        mod.exchange.fetch_ohlcv("RIVAL/USDT", "1m", 50)
        n_rival[0] += 1


th = threading.Thread(target=rival, daemon=True)
th.start()
batch(mod, tickers)
stop.set()
th.join(timeout=5)
oth = field(lines, "خيوطٌ أخرى مسّت")
net = field(lines, "ومنه شبكة")
rn = int(oth.split("الدفعة:")[1].split("نداءً")[0].strip())
check("الخيطُ المنافس مرصود", rn > 0, oth)
check("ولا يُخلَط بنداءات السكانر", "(70 نداء)" in net, net)
check("عدُّه قريبٌ من الحقيقة", abs(rn - n_rival[0]) <= 2,
      "مِسبار=%d حقيقة=%d" % (rn, n_rival[0]))
scan_probe.uninstall(mod)

# ── ④ العطلُ الأوّل: سكانرٌ متعدّدُ الخيوط ───────────────────────────────
print("\n④ سكانرٌ متعدّدُ الخيوط (4 عمّال) — أخطرُ الأعطال الأربعة")
mod = build(plan, delay=0.001)
lines = []
scan_probe.install(mod, log=lines.append)
batch(mod, tickers, workers=4)
head = field(lines, "إجمالاً")
wal, net = field(lines, "_scan_one جداراً"), field(lines, "ومنه شبكة")
oth, srt = field(lines, "خيوطٌ أخرى مسّت"), field(lines, "خرجت مبكّراً")
rev = field(lines, "check_position_reversals")
pct = int(wal.split("(")[1].split("%")[0])
rn = int(oth.split("الدفعة:")[1].split("نداءً")[0].strip())
check("كلُّ النداءات تُنسَب للسكانر", "(70 نداء)" in net, net)
check("وصفرٌ يُنسَب لخيوطٍ غريبة", rn == 0, oth)
check("الفرزُ يبقى 5 و5",
      "(≤2 نداء) 5" in srt and "(≥10) 5" in srt, srt)
check("لا نسبةَ فوق 100%", pct <= 100, wal)
check("ولا فجوةٌ سالبة", "-" not in rev.split("الدالّتين")[1], rev)
check("والتواشي مُعلَن", "تواشٍ" in wal, wal)
check("والمجاميعُ مُعلَنةٌ أنّها مجاميع", "مجاميعُ خيوطٍ" in net, net)
check("وعددُ خيوط المسح مذكور", "خيوطُ مسحٍ 4" in head, head)
scan_probe.uninstall(mod)

# ── ⑤ العطلُ الثاني: دفعةٌ بلا نداءِ انعكاس ─────────────────────────────
print("\n⑤ دفعاتٌ بلا نداءِ انعكاسٍ — سقفٌ وتحذيرٌ لا تراكمٌ صامت")
mod = build(plan)
lines = []
scan_probe.install(mod, log=lines.append)
sc = mod.TechnicalScanner(mod.exchange, mod.plan)
for _ in range(51):                       # 510 عملة > السقف 500
    for t in tickers:
        sc._scan_one(t)
check("التخزينُ لا يتجاوز السقف",
      len(scan_probe._S["coins"]) == scan_probe._MAX_COINS,
      str(len(scan_probe._S["coins"])))
check("والمُسقَطُ معدود", scan_probe._S["dropped"] == 10,
      str(scan_probe._S["dropped"]))
mod.check_position_reversals()
check("والتحذيرُ صريحٌ في الإخراج", "⚠" in " ".join(lines), "لا تحذير")
check("ويقول إنّه يجمع دفعاتٍ لا دفعة",
      "يجمع دفعاتٍ لا دفعةً" in " ".join(lines))
scan_probe.uninstall(mod)

# ── ⑥ العطلُ الثالث: إعادةُ التركيب بعد فقدِ الأصول ─────────────────────
print("\n⑥ إعادةُ التركيب بعد فقدِ الحالة — لا انحدارَ ولا سقوطَ بوت")
mod = build(plan)
lines = []
scan_probe.install(mod, log=lines.append)
check("install ثانياً يُرجع False", scan_probe.install(mod, log=lines.append)
      is False)
wrapped_scan = mod.TechnicalScanner._scan_one
scan_probe._ORIG.clear()                  # يحاكي إعادةَ تحميلِ الوحدة
again = scan_probe.install(mod, log=lines.append)
check("وبعد فقدِ الأصول يرفض التركيب", again is False)
check("ويسمّي السببَ انحداراً", "انحدار" in " ".join(lines))
sc = mod.TechnicalScanner(mod.exchange, mod.plan)
try:
    sc._scan_one("C00/USDT")
    ok = True
except RecursionError:
    ok = False
check("والدالّةُ الملفوفةُ ما زالت تعمل (الأصلُ في الإغلاق)", ok,
      "RecursionError")
check("ولم يُركَّب فوقَ الترقيع",
      mod.TechnicalScanner._scan_one is wrapped_scan)
del mod.TechnicalScanner._scan_one        # تنظيفٌ يدويٌّ بعد فقدِ الأصول
del mod.exchange.fetch_ohlcv
scan_probe._reset(cold=True)

# ── ⑦ الإزالةُ النظيفة ──────────────────────────────────────────────────
print("\n⑦ الإزالةُ النظيفة — بالوراثة وبالملكيّة")
for own in (False, True):
    mod = build(plan, own_scan=own)
    pre = (mod.exchange.fetch_ohlcv, mod.TechnicalScanner._scan_one,
           mod.check_position_reversals)
    scan_probe.install(mod, log=lambda m: None)
    mid = (mod.exchange.fetch_ohlcv, mod.TechnicalScanner._scan_one,
           mod.check_position_reversals)
    scan_probe.uninstall(mod)
    post = (mod.exchange.fetch_ohlcv, mod.TechnicalScanner._scan_one,
            mod.check_position_reversals)
    tag = "own" if own else "موروثة"
    check("[%s] الثلاثُ غُلِّفت" % tag, all(a is not b for a, b in
                                              zip(pre, mid)))
    check("[%s] والثلاثُ عادت" % tag, all(a == b for a, b in zip(pre, post)))
    check("[%s] ولا صفةٌ مخلَّفةٌ على الكائن" % tag,
          "fetch_ohlcv" not in vars(mod.exchange),
          str(list(vars(mod.exchange))))
    check("[%s] ولا وسمُ مِسبارٍ باقٍ" % tag,
          not any(getattr(f, scan_probe._MARK, False) for f in post))
    check("[%s] وإزالةٌ ثانيةٌ آمنة" % tag, scan_probe.uninstall(mod) is False)
check("ولا صفةٌ مخلَّفةٌ على الصنف الموروث",
      "_scan_one" not in vars(build(plan).TechnicalScanner))

# ── ⑧ وحدةٌ ناقصة — يُعلِن ولا يقتل البوت ────────────────────────────────
print("\n⑧ وحدةٌ ناقصة — يُعلِن السببَ ولا يرفع استثناءً")
bad = build(plan, complete=False)
lines = []
try:
    r = scan_probe.install(bad, log=lines.append)
    raised = False
except Exception:                                            # noqa: BLE001
    r, raised = None, True
check("لا استثناءَ افتراضيّاً", not raised)
check("ويُرجع False", r is False)
check("ويسمّي الناقص", "TechnicalScanner" in " ".join(lines))
try:
    scan_probe.install(bad, log=lambda m: None, strict=True)
    strict_raised = False
except RuntimeError:
    strict_raised = True
check("و strict=True يرفع RuntimeError", strict_raised)
check("ولم يبقَ مُركَّباً", scan_probe.installed() is False)

# ── ⑨ استثناءٌ داخل _scan_one ────────────────────────────────────────────
print("\n⑨ استثناءٌ داخل _scan_one — يُسجَّل ولا يُعلَّق")
mod = build(plan)


class Boom(mod.TechnicalScanner):
    def _scan_one(self, ticker):
        self.ex.fetch_ohlcv(ticker, "1h", 200)
        raise RuntimeError("فشلٌ شبكيٌّ مُحاكى")


mod.TechnicalScanner = Boom
lines = []
scan_probe.install(mod, log=lines.append)
for t in tickers[:3]:
    try:
        mod.TechnicalScanner(mod.exchange, mod.plan)._scan_one(t)
    except RuntimeError:
        pass
check("العملاتُ المخفِقةُ مسجَّلة", len(scan_probe._S["coins"]) == 3,
      str(len(scan_probe._S["coins"])))
check("ولا عملةٌ معلَّقةٌ على الخيط",
      getattr(scan_probe._TL, "cur", None) is None)
mod.check_position_reversals()
check("والإخراجُ يصدر بعدها", any("إجمالاً" in L for L in lines))
scan_probe.uninstall(mod)

# ── ⑩ كلفةُ المِسبار نفسِه ──────────────────────────────────────────────
print("\n⑩ كلفةُ المِسبار — لفٌّ وعدّاد، لا أكثر")
mod = build(plan)
sc = mod.TechnicalScanner(mod.exchange, mod.plan)
t0 = time.perf_counter()
for _ in range(200):
    sc._scan_one("C09/USDT")
bare = time.perf_counter() - t0
scan_probe.install(mod, log=lambda m: None)
sc = mod.TechnicalScanner(mod.exchange, mod.plan)
t0 = time.perf_counter()
for _ in range(200):
    sc._scan_one("C09/USDT")
wrapped = time.perf_counter() - t0
scan_probe.uninstall(mod)
per_call = (wrapped - bare) / (200 * 13) * 1e6       # 12 نداء + الدالّة
print("      بلا مِسبار %.1f م.ث · به %.1f م.ث ⇒ %.1f ميكروثانية/نداء"
      % (bare * 1e3, wrapped * 1e3, per_call))
check("الكلفةُ دون 50 ميكروثانية للنداء", per_call < 50,
      "%.1f" % per_call)

# ── ⑪ المشتبَهُ الثالث: _mtf_card_fields ────────────────────────────────
print("\n⑪ _mtf_card_fields — تُلَفّ إن وُجدت، وتُفرَز بموضع النداء")
mod = build(plan)
card_calls = []


def _mtf_card_fields(ticker):
    card_calls.append(ticker)
    time.sleep(0.001)
    return {"ticker": ticker, "mtf_story": "…"}


mod._mtf_card_fields = _mtf_card_fields


class CardScanner(_BaseScanner):
    def _scan_one(self, ticker):
        self.ex.fetch_ohlcv(ticker, "1h", 200)
        mod._mtf_card_fields(ticker)              # داخل المسح
        return {"ticker": ticker}


mod.TechnicalScanner = CardScanner
lines = []
scan_probe.install(mod, log=lines.append)
check("يُعلِن أنّه وجدها",
      not any("لم أجد _mtf_card_fields" in L for L in lines))
sc = mod.TechnicalScanner(mod.exchange, mod.plan)
for t in tickers:
    sc._scan_one(t)                               # 10 داخل المسح
for t in tickers[:3]:
    mod._mtf_card_fields(t)                       # 3 بين الدالّتين


def panel():
    for t in tickers[:2]:
        mod._mtf_card_fields(t)                   # 2 من خيط لوحة


pth = threading.Thread(target=panel)
pth.start()
pth.join(timeout=5)
mod.check_position_reversals()
cl = field(lines, "_mtf_card_fields")
check("عدُّها الكلّيُّ 15", "15 نداءً" in cl, cl)
check("داخلَ المسح 10", "داخل المسح 10" in cl, cl)
check("وبين الدالّتين 3", "بين الدالّتين 3" in cl, cl)
check("ومن خيوطٍ أخرى 2", "خيوطٌ أخرى 2" in cl, cl)
check("والنداءاتُ الفعليّةُ 15", len(card_calls) == 15, str(len(card_calls)))
scan_probe.uninstall(mod)
check("وتعود كما كانت", mod._mtf_card_fields is _mtf_card_fields)
check("ولا وسمَ باقياً",
      not getattr(mod._mtf_card_fields, scan_probe._MARK, False))

print("\n⑫ وحدةٌ بلا _mtf_card_fields — يُعلِن أنّ الفجوةَ تبقى بلا اسم")
mod = build(plan)
lines = []
scan_probe.install(mod, log=lines.append)
check("يُعلِن غيابَها صراحةً",
      any("لم أجد _mtf_card_fields" in L for L in lines))
batch(mod, tickers)
check("ولا سطرَ بطاقةٍ في الإخراج",
      not any("_mtf_card_fields" in L for L in lines if "إجمالاً" not in L
              and "لم أجد" not in L))
scan_probe.uninstall(mod)
check("ولا صفةَ بطاقةٍ مخلَّفة", not hasattr(mod, "_mtf_card_fields"))

# ── ⑬ حالاتٌ حدّيّة — من مراجعةٍ خصِمةٍ للمِسبار نفسِه ───────────────────
print("\n⑬ حالاتٌ حدّيّة — وكلُّ واحدةٍ أسقطت شيئاً قبل إصلاحه")

# ⑬-أ المعادلةُ لا تكذب: الجدارُ سطرٌ والمجاميعُ سطرٌ يقول إنّه مجموع
mod = build({t: 3 for t in tickers}, delay=0.002)
lines = []
scan_probe.install(mod, log=lines.append)
batch(mod, tickers, workers=6)
wal, net = field(lines, "_scan_one جداراً"), field(lines, "ومنه شبكة")
wall_pct = int(wal.split("(")[1].split("%")[0])
check("الجدارُ لا يتجاوز الدفعة", wall_pct <= 100, wal)
check("ولا يدّعي أنّ الجدارَ = شبكة + حساب", "=" not in wal, wal)
check("والمجاميعُ موسومةٌ بأنّها مجاميع", "مجاميعُ خيوطٍ" in net, net)
scan_probe.uninstall(mod)

# ⑬-ب بطاقةٌ خارج الدفعة لا تُنسَب إلى «خيوطٍ أخرى»
mod = build(plan)
mod._mtf_card_fields = lambda t: t
lines = []
scan_probe.install(mod, log=lines.append)
mod._mtf_card_fields("C00/USDT")            # قبل أوّل مسح ⇒ خارج الدفعة
batch(mod, tickers)
cl = field(lines, "_mtf_card_fields ")
check("تُعَدّ خارجَ الدفعة", "خارج الدفعة 1" in cl, cl)
check("ولا تُنسَب لخيوطٍ أخرى", "خيوطٌ أخرى 0" in cl, cl)
scan_probe.uninstall(mod)

# ⑬-ج استثناءٌ في نداء الانعكاس: يمرّ · والسطرُ يُخرَج · والحالةُ تُصفَّر
mod = build(plan)
_ok_cpr = mod.check_position_reversals


def _boom_cpr():
    _ok_cpr()
    raise RuntimeError("انفجارُ الانعكاس")


mod.check_position_reversals = _boom_cpr
lines = []
scan_probe.install(mod, log=lines.append)
sc = mod.TechnicalScanner(mod.exchange, mod.plan)
for t in tickers:
    sc._scan_one(t)
raised = False
try:
    mod.check_position_reversals()
except RuntimeError:
    raised = True
check("الاستثناءُ يمرّ إلى البوت كما هو", raised)
check("والسطرُ يُخرَج رغمه", any("إجمالاً" in L for L in lines))
check("والحالةُ صُفِّرت", scan_probe._S["t0"] is None
      and not scan_probe._S["coins"])
scan_probe.uninstall(mod)

# ⑬-د دالّةُ السجلّ تفشل — لا تتسرّب الحالة ولا ينفجر البوت
mod = build(plan)
seen = []


def _bad_log(m):
    seen.append(m)
    if "إجمالاً" in m:
        raise IOError("القرصُ ممتلئ")


scan_probe.install(mod, log=_bad_log)
sc = mod.TechnicalScanner(mod.exchange, mod.plan)
for t in tickers:
    sc._scan_one(t)
mod.check_position_reversals()               # لا يرفع
check("فشلُ السجلّ لا يصل البوت", True)
check("والحالةُ صُفِّرت رغمه", scan_probe._S["t0"] is None
      and not scan_probe._S["coins"])
scan_probe.uninstall(mod)

# ⑬-هـ _scan_one متداخلةٌ مع نفسها — العملةُ الجارية تعود لأبيها
mod = build(plan)


class _Nest(mod.TechnicalScanner):
    def _scan_one(self, ticker):
        self.ex.fetch_ohlcv(ticker, "1h", 200)
        if ticker == "C00/USDT":
            type(self)._scan_one(self, "C09/USDT")
        return ticker


mod.TechnicalScanner = _Nest
lines = []
scan_probe.install(mod, log=lines.append)
mod.TechnicalScanner(mod.exchange, mod.plan)._scan_one("C00/USDT")
check("لا عملةَ معلَّقةٌ بعد التداخل",
      getattr(scan_probe._TL, "cur", None) is None)
mod.check_position_reversals()
check("وعُدَّت عملتان", "2 عملة" in field(lines, "إجمالاً"),
      field(lines, "إجمالاً"))
scan_probe.uninstall(mod)

# ⑬-و الإزالةُ بلا وسيط · وصفةٌ مملوكةٌ على الكائن · ودفعةٌ بصفرِ عملات
mod = build(plan)
pre_cpr = mod.check_position_reversals
scan_probe.install(mod, log=lambda m: None)
check("uninstall() بلا وسيطٍ تعمل", scan_probe.uninstall() is True)
check("وتعيد نداءَ الانعكاس", mod.check_position_reversals is pre_cpr)

mod = build(plan)


def _own_fetch(symbol, timeframe="1h", limit=100):
    return [[0, 1.0, 2.0, 0.5, 1.5, 10.0]]


mod.exchange.fetch_ohlcv = _own_fetch        # صفةٌ مملوكةٌ لا موروثة
scan_probe.install(mod, log=lambda m: None)
scan_probe.uninstall(mod)
check("الصفةُ المملوكةُ تعود هي نفسُها",
      mod.exchange.fetch_ohlcv is _own_fetch)

mod = build(plan)
lines = []
scan_probe.install(mod, log=lines.append)
mod.check_position_reversals()               # نداءُ انعكاسٍ بلا مسح
check("دفعةٌ بصفرِ عملاتٍ لا تُخرج سطراً كاذباً",
      not any("إجمالاً" in L for L in lines), str(lines[1:]))
scan_probe.uninstall(mod)

# ── الحصيلة ────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
print("الحصيلة: %d/%d" % (len(PASSED), len(PASSED) + len(FAILED)))
if FAILED:
    print("الساقط:")
    for f in FAILED:
        print("  ✗ " + f)
print("=" * 66)
sys.exit(1 if FAILED else 0)
