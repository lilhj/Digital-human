"""小米商城商品爬虫（个人学习/demo 用途）。

爬取公开商品信息：名称、价格、描述、图片，保存到本目录。
- 接口：https://m.mi.com/v1/product/all_product?cat_id=X
- 字段：name / price(元) / product_desc / product_id / puzzle_url(图片)
- 价格统一转「分」（整数），供电商 demo 直接入库。
- 图片下载到 images/ 子目录，按 product_id 命名。

仅用标准库（urllib），无第三方依赖。控制请求频率，不碰登录态/个人信息。
"""
import json
import os
import re
import sys
import time
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
REFERER = "https://m.mi.com/"
BASE = os.path.dirname(os.path.abspath(__file__))
IMG_DIR = os.path.join(BASE, "images")

# 实体商品分类（排除「小米服务」「有品精选」两个非实体类）
CATEGORIES = {
    "1242": "Xiaomi手机",
    "1243": "REDMI手机",
    "1251": "手机配件",
    "2701": "平板",
    "874": "智能穿戴",
    "459": "电脑办公",
    "458": "电视",
    "2537": "空调",
    "2783": "洗衣机",
    "1821": "冰箱",
    "1186": "厨房大电",
    "1047": "小家电",
    "1053": "智能家居",
    "871": "出行运动",
    "2484": "车周边",
    "466": "日用百货",
    "1844": "儿童用品",
}


def fetch_json(url: str):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REFERER})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1)


def price_to_cents(p) -> int:
    """价格字符串(元) -> 分(整数)。兼容 '149' / '99.9' / '1499 起' / 空。"""
    m = re.search(r"[\d.]+", str(p))
    if not m:
        return 0
    return int(round(float(m.group()) * 100))


def ext_from_url(url: str) -> str:
    path = url.split("?")[0]
    ext = os.path.splitext(path)[1].lower()
    return ext if ext in (".png", ".jpg", ".jpeg", ".webp") else ".png"


def download_img(url: str, path: str) -> bool:
    # 沙箱环境对小米 CDN 的 HTTPS 握手会卡死，改走 HTTP 即可正常下载
    url = url.replace("https://", "http://")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REFERER})
        with urllib.request.urlopen(req, timeout=20) as r, open(path, "wb") as f:
            f.write(r.read())
        return True
    except Exception:
        return False


def main(max_per_cat: int):
    os.makedirs(IMG_DIR, exist_ok=True)
    all_products = []
    img_ok = 0
    img_fail = 0

    for cid, cname in CATEGORIES.items():
        try:
            data = fetch_json(f"https://m.mi.com/v1/product/all_product?cat_id={cid}")
            products = data["data"]["product"]
        except Exception as e:
            print(f"[skip] {cname}({cid}): {e}", flush=True)
            continue

        picked = products[:max_per_cat]
        for p in picked:
            pid = p.get("product_id", "")
            name = p.get("name", "")
            price_cents = price_to_cents(p.get("price", ""))
            desc = p.get("product_desc", "")
            img_url = p.get("puzzle_url", "")

            local_img = ""
            if img_url:
                fname = f"{pid}{ext_from_url(img_url)}"
                fpath = os.path.join(IMG_DIR, fname)
                if download_img(img_url, fpath):
                    local_img = f"images/{fname}"
                    img_ok += 1
                else:
                    local_img = img_url  # 下载失败回退为原始 URL
                    img_fail += 1

            all_products.append({
                "product_id": pid,
                "name": name,
                "price_cents": price_cents,
                "price_yuan": round(price_cents / 100, 2),
                "category": cname,
                "category_id": int(cid),
                "description": desc,
                "image": local_img,
                "source_url": img_url,
            })

        print(f"[ok] {cname}({cid}): {len(picked)} 个商品", flush=True)
        time.sleep(0.5)  # 分类间限速

    out = os.path.join(BASE, "products.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(all_products, f, ensure_ascii=False, indent=2)

    print(f"\n完成：共 {len(all_products)} 个商品 | 图片成功 {img_ok} 张，失败 {img_fail} 张")
    print(f"数据文件：{out}")
    print(f"图片目录：{IMG_DIR}")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    main(n)
