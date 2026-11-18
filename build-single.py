# -*- coding: utf-8 -*-
"""
把 index-source.html（外部引用 assets/）+ assets/ 打包成单文件 index.html。

原则：每张图在文件里只存一份 data URI
- 动态拼接的图（cast/face/ep/av）→ 放进 ASSET 表，由 A() 取用
- 静态图被多次引用 → 也放进表，引用处用 data-asset，启动时赋值
- 只被引用一次的静态图 → 直接内联到 src
- CSS 背景图 → 直接内联（CSS 无法查表）
用法： python build-single.py
"""
import base64
import io
import os
import re

from PIL import Image

SRC = "index-source.html"
OUT = "index.html"
ASSETS = "assets"
PLACEHOLDER = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
DYNAMIC_DIRS = ("cast/", "face/", "ep/", "av/")

WIDTH_LIMIT = {
    "axel.jpg": 1200,
    "axel-map.jpg": 1150,
    "shrine.jpg": 1150,
    "explosion.jpg": 1150,
    "keyvisual.jpg": 800,
}


def encode(path):
    rel = path.replace("\\", "/")
    name = os.path.basename(rel)
    im = Image.open(path)
    limit = WIDTH_LIMIT.get(name)

    if im.mode == "RGBA":
        if limit and im.width > limit:
            im = im.resize((limit, round(im.height * limit / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=86, method=6)
        mime = "image/webp"
    else:
        im = im.convert("RGB")
        if limit and im.width > limit:
            im = im.resize((limit, round(im.height * limit / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80, optimize=True, progressive=True)
        mime = "image/jpeg"
    raw = buf.getvalue()
    return mime, base64.b64encode(raw).decode(), len(raw)


def main():
    html = open(SRC, encoding="utf-8").read()

    # ---------- 1. 编码 ----------
    uri_of, raw_of = {}, {}
    for root, _, files in os.walk(ASSETS):
        for f in sorted(files):
            if not f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                continue
            p = os.path.join(root, f)
            mime, b64, raw_len = encode(p)
            key = p.replace("\\", "/")[len(ASSETS) + 1:]
            uri_of[key] = "data:%s;base64,%s" % (mime, b64)
            raw_of[key] = raw_len

    # ---------- 2. 去掉 srcset ----------
    html = re.sub(r'\ssrcset="[^"]*"', "", html)
    html = re.sub(r'\ssizes="[^"]*"', "", html)

    # ---------- 3. JS 动态拼接 → A() ----------
    js_repl = [
        ("assets/cast/'+c.id+'-v2.png", "'+A('cast/'+c.id+'-v2.png')+'"),
        ("'assets/cast/' + q.fig + '-v2.png'", "A('cast/'+q.fig+'-v2.png')"),
        ("assets/face/'+best+'-v2.png", "'+A('face/'+best+'-v2.png')+'"),
        ("assets/ep/'+ARC_IMGS[i]+'.jpg", "'+A('ep/'+ARC_IMGS[i]+'.jpg')+'"),
        ("assets/ep/ep'+no+'.jpg", "'+A('ep/ep'+no+'.jpg')+'"),
        ("assets/av/'+s.img+'.png", "'+A('av/'+s.img+'.png')+'"),
        ("assets/av/'+q.av+'.png", "'+A('av/'+q.av+'.png')+'"),
        ("assets/av/'+btn.dataset.k+'.png", "'+A('av/'+btn.dataset.k+'.png')+'"),
    ]
    for old, new in js_repl:
        if old not in html:
            print("  [警告] 未找到拼接片段：", old)
        html = html.replace(old, new)

    # ---------- 4. 统计静态引用次数 ----------
    srcs = re.findall(r'src="(assets/[A-Za-z0-9_./-]+\.(?:png|jpg|jpeg|webp))"', html)
    css_urls = set(re.findall(r'url\("(assets/[A-Za-z0-9_./-]+\.(?:png|jpg|jpeg|webp))"\)', html))
    counts = {}
    for s in srcs:
        counts[s] = counts.get(s, 0) + 1

    # 需要放进表的：动态目录、或静态被多次引用
    table_keys = set()
    for key in uri_of:
        if key.startswith(DYNAMIC_DIRS) or counts.get("assets/" + key, 0) > 1:
            table_keys.add(key)

    # ---------- 5. 静态引用处理 ----------
    def src_repl(m):
        path = m.group(1)
        key = path[len("assets/"):]
        if key in table_keys:
            return 'src="%s" data-asset="%s"' % (PLACEHOLDER, key)
        return 'src="%s"' % uri_of[key]

    html = re.sub(r'src="(assets/[A-Za-z0-9_./-]+\.(?:png|jpg|jpeg|webp))"', src_repl, html)

    # CSS 背景：直接内联（若有对应 key，则不再重复放进表）
    def css_repl(m):
        path = m.group(1)
        key = path[len("assets/"):]
        table_keys.discard(key)
        return 'url("%s")' % uri_of[key]

    html = re.sub(r'url\("(assets/[A-Za-z0-9_./-]+\.(?:png|jpg|jpeg|webp))"\)', css_repl, html)

    # ---------- 6. 注入 ASSET 表 ----------
    table = {k: uri_of[k] for k in table_keys}
    js_table = ",\n".join("  %r: %r" % (k, v) for k, v in table.items())
    inject = (
        "<script>\n"
        "/* 单文件版：图片内联，不依赖任何外部路径 */\n"
        "var ASSET = {\n" + js_table + "\n};\n"
        "function A(k){ return ASSET[k] || ('assets/' + k); }\n"
        "</script>\n"
    )
    html = html.replace("<script>", inject + "<script>", 1)

    anchor = ("  var esc = function(s){return String(s).replace(/&/g,'&amp;')"
              ".replace(/</g,'&lt;').replace(/>/g,'&gt;');};\n")
    assert anchor in html
    html = html.replace(
        anchor,
        anchor + "  $$('[data-asset]').forEach(function(el){ el.src = A(el.dataset.asset); });\n",
        1,
    )

    # ---------- 7. 校验 ----------
    leftovers = re.findall(r"assets/[A-Za-z0-9_./-]+\.(?:png|jpg|jpeg|webp)", html)
    print("内联 %d 张（其中查表 %d 张），残留外部路径：%s"
          % (len(uri_of), len(table), leftovers or "无"))
    print("原图合计 %.2f MB" % (sum(raw_of.values()) / 1024 / 1024))

    open(OUT, "w", encoding="utf-8").write(html)
    print("输出 %s：%.2f MB" % (OUT, os.path.getsize(OUT) / 1024 / 1024))


if __name__ == "__main__":
    main()
