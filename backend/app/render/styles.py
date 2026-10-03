"""Stylesheet and the in-browser fitting script shared by the day and week pictures.

Split from `templates` so the HTML builders there stay readable: this is the part that
only changes when the design does.
"""

from __future__ import annotations

FRAME_SELECTOR = ".frame"
WIDTH = 1080

TITLE_MAX_LINES = 2
"""A card title longer than this many lines on the day picture is swapped for its short form."""
WEEK_NAME_MIN_PX = 20
"""A week row's name that does not fit shrinks down to this size before it is allowed to wrap."""

# Runs in the browser, once the fonts are in (see the renderer): only real layout can say
# whether a text fits. Day card titles that have a shorter form carry it in `data-short`.
# A week row's name is never cut with "…": it shrinks a little, and only as a last
# resort wraps onto a second line.
FIT_SCRIPT = """
window.fitTitles = function () {
  document.querySelectorAll('.title[data-short]').forEach(function (el) {
    var line = parseFloat(getComputedStyle(el).lineHeight);
    if (el.getBoundingClientRect().height > line * __LINES__ + 1) {
      el.textContent = el.dataset.short;
    }
  });
  document.querySelectorAll('.ln .n').forEach(function (el) {
    var size = parseFloat(getComputedStyle(el).fontSize);
    while (el.scrollWidth > el.clientWidth + 1 && size > __MIN__) {
      size -= 1;
      el.style.fontSize = size + 'px';
    }
    if (el.scrollWidth > el.clientWidth + 1) {
      el.style.whiteSpace = 'normal';
      el.style.textOverflow = 'clip';
    }
  });
};
""".replace("__LINES__", str(TITLE_MAX_LINES)).replace("__MIN__", str(WEEK_NAME_MIN_PX))

CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{background:#f4efe6;font-family:Onest,system-ui,sans-serif}
.frame{width:1080px;min-height:var(--h);padding:64px;display:flex;flex-direction:column;
  gap:var(--gap);background:#f4efe6;color:#1a1611;overflow:hidden}
.mono{font-family:'JetBrains Mono',ui-monospace,monospace}
.top{display:flex;align-items:center;justify-content:space-between}
.eyebrow{font-size:22px;line-height:28px;font-weight:600;letter-spacing:.08em;
  text-transform:uppercase;color:#5c5347}
.pill{font-size:22px;line-height:28px;font-weight:600;padding:6px 16px;
  border:1px solid #8a7f70;border-radius:999px}
h1{font-family:Unbounded,'Arial Black',sans-serif;font-size:96px;line-height:100px;
  font-weight:700;letter-spacing:-.02em}
.sub{font-size:30px;line-height:40px;color:#5c5347}
.sub b{font-family:'JetBrains Mono',monospace;font-size:28px;font-weight:400;color:#1a1611}
.strip{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px}
.chip{display:flex;align-items:center;justify-content:center;gap:8px;height:56px;
  border-radius:999px;border:1px solid #8a7f70;font-size:22px;font-weight:600;
  letter-spacing:.04em;font-family:'JetBrains Mono',monospace}
.chip span:first-child{text-transform:uppercase}
.chip span:last-child{font-weight:400}
.chip.on{background:#ff4d2e;border-color:#ff4d2e}
.chip.off{background:#228B22;border-color:#228B22;color:#fff}
.list{display:flex;flex-direction:column;gap:16px;flex-grow:1}
.card{display:grid;grid-template-columns:168px minmax(0,1fr) auto;background:#fbf8f2;
  border:1px solid #d9cfbf;border-radius:10px;padding:24px 28px;flex-grow:1;align-items:center}
.time{display:flex;flex-direction:column;gap:6px;padding-right:24px;
  border-right:1px solid #d9cfbf;align-self:stretch;justify-content:center;
  font-family:'JetBrains Mono',monospace}
.time .s{font-size:36px;line-height:40px;font-weight:600}
.time .e{font-size:24px;line-height:28px;color:#5c5347}
.body{display:flex;flex-direction:column;gap:10px;padding-left:28px;min-width:0}
.tags{display:flex;align-items:center;gap:12px;font-family:'JetBrains Mono',monospace;
  font-size:20px;line-height:24px;font-weight:600;letter-spacing:.08em;
  text-transform:uppercase;color:#5c5347}
.tag{font-size:18px;line-height:20px;padding:3px 10px;border:1px solid #8a7f70;
  border-radius:4px;color:#1a1611}
.badge{font-family:'JetBrains Mono',monospace;font-size:18px;line-height:20px;font-weight:600;
  letter-spacing:.08em;text-transform:uppercase;padding:5px 12px;border-radius:4px}
.badge.blue{background:#d9e6fa;color:#1d3f75}
.badge.green{background:#d8ecd5;color:#21502a}
.badge.red{background:#f8d2ca;color:#8c2313}
.badge.gray{background:#ece5d8;color:#5c5347}
.time .pn{margin-top:10px;font-size:18px;line-height:22px;letter-spacing:.08em;
  text-transform:uppercase;color:#5c5347}
.place{display:flex;flex-direction:column;gap:4px;padding-left:28px;width:238px;
  border-left:1px solid #d9cfbf;align-self:stretch;justify-content:center}
.place .lbl,.place .bld{font-size:20px;line-height:26px;color:#5c5347}
.place .num{font-size:44px;line-height:48px;font-weight:700;overflow-wrap:anywhere}
.tag.stream{border-style:dashed}
.legend{display:flex;flex-direction:column;gap:8px;font-size:22px;line-height:28px;
  color:#5c5347}
.legend .tag{margin-right:10px;font-family:'JetBrains Mono',monospace;font-weight:600}
.title{font-family:Unbounded,'Arial Black',sans-serif;font-size:27px;line-height:34px;
  font-weight:600}
.meta{font-size:24px;line-height:30px;color:#5c5347}
.meta.unknown{font-style:italic}
.empty{flex-grow:1;display:flex;align-items:center;justify-content:center;
  background:#fbf8f2;border:1px dashed #d9cfbf;border-radius:10px;font-size:34px;
  color:#5c5347}
.empty.off{font-size:64px;line-height:72px;font-weight:800;color:#1a1611}
.foot{display:flex;align-items:center;justify-content:space-between;padding-top:24px;
  border-top:1px solid #d9cfbf}
.logo{font-family:Unbounded,'Arial Black',sans-serif;font-size:30px;line-height:36px;
  font-weight:700}
.logo span{color:#ff4d2e}
.upd{font-size:22px;line-height:28px;color:#5c5347}
.days{display:flex;flex-direction:column;gap:12px;flex-grow:1}
.row{display:grid;grid-template-columns:164px minmax(0,1fr);background:#fbf8f2;
  border:1px solid #d9cfbf;border-radius:10px;padding:20px 24px;align-items:center}
.who{display:flex;flex-direction:column;align-items:flex-start;gap:6px;padding-right:12px;
  border-right:1px solid #d9cfbf;align-self:stretch;justify-content:center}
.dow{font-family:Onest,system-ui,sans-serif;font-size:76px;line-height:72px;font-weight:800;
  letter-spacing:-.02em;text-transform:uppercase}
.dat{font-size:22px;line-height:28px;color:#5c5347;white-space:nowrap}
.ls{display:flex;flex-direction:column;gap:10px;padding-left:16px;min-width:0}
.ln{display:grid;grid-template-columns:84px minmax(0,1fr) auto;gap:12px;align-items:baseline}
.ln .t{font-family:'JetBrains Mono',monospace;font-size:24px;line-height:30px;font-weight:600}
.ln .n{font-size:26px;line-height:30px;font-weight:500;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.ln .r{font-family:'JetBrains Mono',monospace;font-size:20px;line-height:30px;color:#5c5347}
.ln.exam .t{color:#ff4d2e}
.k-blue{--dot:#3b6fd1}.k-green{--dot:#3f9a4c}.k-red{--dot:#e0442a}.k-gray{--dot:#a69a88}
.ln .n.kd::after{content:"";display:inline-block;width:14px;height:14px;margin-left:12px;
  border-radius:50%;vertical-align:middle;background:var(--dot)}
.dot{display:inline-block;flex:none;width:14px;height:14px;border-radius:50%;background:var(--dot)}
.kinds{display:flex;flex-wrap:wrap;gap:12px 32px;font-size:22px;line-height:28px;color:#5c5347}
.kinds span{display:inline-flex;align-items:center;gap:10px}
.wd{display:flex;flex-direction:column;gap:6px}
.row.off .wd{align-self:stretch;background:#228B22;border-radius:8px;padding:8px 10px;
  margin-left:-10px}
.row.off .dow{color:#fff}
.row.off .dat{color:#e6f4e6}
.none{font-size:24px;line-height:30px;color:#5c5347}
.tag.retake{background:#ff4d2e;border-color:#ff4d2e}
.card.retake{flex-grow:0;background:#fff1ec;border-color:#ff4d2e}
.card.retake .title{font-size:24px;line-height:32px}
.ln.retake{grid-template-columns:84px auto minmax(0,1fr) auto;gap:12px;align-items:center;
  background:#fde9e3;border-radius:10px;padding:8px 14px;margin:0 -14px}
.ln.retake .t{color:#ff4d2e}
.ln.retake .tag{font-family:'JetBrains Mono',monospace;font-weight:600;letter-spacing:.06em;
  font-size:16px;line-height:18px;padding:4px 8px}
"""
