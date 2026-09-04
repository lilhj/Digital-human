"""生成演示用虚拟凭证图片：破损照片 / 快递单 / 发票。

仅用于本地开发联调：买家测上传凭证、OCR、退款工作流时手头没有真实图片。
用 Pillow 纯矢量绘制，无任何真实个人信息（地址/单号/电话均为虚构）。

产物目录：<项目根>/sample_evidence/
  broken_screen_phone.png   破损照片-碎屏手机（屏幕 1/3 碎）+ 裂纹
  scratched_case.png        破损照片-机身划痕耳机盒
  broken_beam_washer.png    破损照片-小家电外壳破损
  express_waybill.png       快递单-顺丰式运单（条形码 + 三段地址）
  express_package.jpg       快递单-有平铺包裹背景的运单
  invoice_goods.png         发票-增值税普通发票（商品类，价税分开）
  invoice_freight.png       发票-运费发票（物流专票样式）

用法: cd backend && python scripts/gen_sample_evidence.py
"""
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = PROJECT_ROOT / "sample_evidence"

FONT_HEI = "C:/Windows/Fonts/simhei.ttf"  # 黑体：标题工具栏
FONT_SONG = "C:/Windows/Fonts/simsun.ttc"  # 宋体：正文表格


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def text_w(draw: ImageDraw.ImageDraw, s: str, f: ImageFont.FreeTypeFont) -> int:
    return draw.textlength(s, font=f)


# ---------------------------------------------------------------- 破损照片
def gen_broken_screen_phone() -> None:
    W, H = 1200, 1200
    img = Image.new("RGB", (W, H), (212, 214, 219))  # 浅灰桌面
    d = ImageDraw.Draw(img)
    rnd = random.Random(7)

    # 桌面纹理
    for _ in range(1600):
        x, y = rnd.randrange(W), rnd.randrange(H)
        img.putpixel((x, y), tuple(c + rnd.randint(-8, 8) for c in (212, 214, 219)))

    # 手机机身（圆角矩形 + 侧边金属边）
    px, py, pw, ph = W // 2 - 190, H // 2 - 400, 380, 800
    d.rounded_rectangle([px - 8, py - 8, px + pw + 8, py + ph + 8], 42, fill=(58, 62, 66))
    d.rounded_rectangle([px, py, px + pw, py + ph], 36, fill=(24, 26, 29))

    # 屏幕
    sx, sy, sw, sh = px + 18, py + 26, pw - 36, ph - 52
    d.rounded_rectangle([sx, sy, sx + sw, sy + sh], 20, fill=(16, 18, 22))
    # 屏幕壁纸（深蓝渐变 + 圆点图案）
    for y in range(sy, sy + sh):
        t = (y - sy) / sh
        col = (int(20 + 20 * t), int(30 + 50 * t), int(70 + 110 * t))
        d.line([sx, y, sx + sw, y], fill=col)
    for i in range(60):
        x, y = rnd.randrange(sx, sx + sw), rnd.randrange(sy, sy + sh)
        r = rnd.choice((2, 3, 4))
        d.ellipse([x, y, x + r * 2, y + r * 2], fill=(46, 59, 92))
    # 返回键车道（黑条）
    d.rectangle([sx, sy + sh - 70, sx + sw, sy + sh], fill=(12, 13, 17))

    # 裂纹：从右上冲击点发散
    cxs, cys = sx + int(sw * 0.72), sy + int(sh * 0.20)
    for j in range(80):
        ang = rnd.uniform(0, math.tau)
        L = rnd.uniform(8, 90) ** 1.15
        x0, y0 = cxs, cys
        x1, y1 = cxs + math.cos(ang) * L, cys + math.sin(ang) * L
        seg = 7
        while (math.hypot(x1 - x0, y1 - y0) > 3) and seg > 0:
            d.line([x0, y0, x1, y1], fill=(0, 0, 0), width=2)
            if rnd.random() < 0.75:  # 分叉
                bx = x1 + math.cos(ang + rnd.uniform(-0.5, 0.5)) * L * 0.3
                by = y1 + math.sin(ang + rnd.uniform(-0.5, 0.5)) * L * 0.3
                d.line([x1, y1, bx, by], fill=(0, 0, 0), width=1)
            x0, y0, x1, y1 = x1, y1, x1 + math.cos(ang) * 12, y1 + math.sin(ang) * 12
            seg -= 1
    # 碎裂区 x5
    for k in range(5):
        fx, fy = cxs + rnd.randint(-60, 60), cys + rnd.randint(-40, 110)
        for j in range(6):
            ang2 = rnd.uniform(0, math.tau)
            d.line([fx, fy, fx + math.cos(ang2) * rnd.uniform(4, 14), fy + math.sin(ang2) * rnd.uniform(4, 14)], fill=(0, 0, 0), width=1)
    # 亮破碎反向高光（更真实）
    for k in range(30):
        x = cxs + rnd.randint(-40, 40)
        y = cys + rnd.randint(-20, 40)
        d.line([x, y, x + rnd.randint(1, 5), y + rnd.randint(-2, 2)], fill=(120, 140, 170), width=1)

    # 摄像头模组
    d.rounded_rectangle([px + pw - 70, py + 10, px + pw - 6, py + 50], 10, fill=(40, 42, 46))
    d.ellipse([px + pw - 58, py + 18, px + pw - 38, py + 38], fill=(4, 5, 8))
    d.ellipse([px + pw - 32, py + 18, px + pw - 14, py + 38], fill=(4, 5, 8))

    img = img.filter(ImageFilter.GaussianBlur(0.4)) if False else img
    img.save(OUT_DIR / "broken_screen_phone.png")


def gen_scratched_case() -> None:
    W, H = 1200, 1200
    img = Image.new("RGB", (W, H), (198, 200, 205))
    d = ImageDraw.Draw(img)
    rnd = random.Random(11)
    for _ in range(1400):
        x, y = rnd.randrange(W), rnd.randrange(H)
        img.putpixel((x, y), tuple(c + rnd.randint(-10, 10) for c in (198, 200, 205)))

    cx, cy, r = W // 2, H // 2, 320
    # 耳机盒盖（白色圆角方）
    d.rounded_rectangle([cx - r, cy - r, cx + r, cy + r], 90, fill=(245, 245, 248), outline=(150, 152, 158), width=3)
    # 内衬（深灰）
    d.rounded_rectangle([cx - r + 40, cy - r + 40, cx + r - 40, cy + r - 40], 66, fill=(52, 54, 58))
    # 左右耳机槽
    d.rounded_rectangle([cx - 120, cy - 150, cx - 20, cy - 50], 18, fill=(20, 21, 24))
    d.rounded_rectangle([cx + 20, cy - 150, cx + 120, cy - 50], 18, fill=(20, 21, 24))
    # 中央指示灯
    d.ellipse([cx - 14, cy + 40, cx + 14, cy + 68], fill=(8, 9, 12))

    # 划痕：交错的灰色划线条纹
    for i in range(40):
        a0 = rnd.uniform(0, math.tau)
        x = cx + rnd.randint(-r, r)
        y = cy + rnd.randint(-r, r)
        ln = rnd.uniform(30, 160)
        col = rnd.choice([(150, 152, 156), (172, 174, 178), (135, 137, 142)])
        w = rnd.choice([1, 1, 2])
        d.line([x, y, x + math.cos(a0) * ln, y + math.sin(a0) * ln], fill=col, width=w)
    # 左上角磕碰凹陷
    d.arc([cx - r + 20, cy - r + 20, cx - r + 150, cy - r + 150], 90, 270, fill=(120, 122, 127), width=6)

    img.save(OUT_DIR / "scratched_case.png")


def gen_broken_washer() -> None:
    W, H = 1200, 1200
    img = Image.new("RGB", (W, H), (206, 205, 200))
    d = ImageDraw.Draw(img)
    rnd = random.Random(23)
    for _ in range(1500):
        x, y = rnd.randrange(W), rnd.randrange(H)
        img.putpixel((x, y), tuple(c + rnd.randint(-10, 10) for c in (206, 205, 200)))

    cx, cy = W // 2, H // 2
    # 机身（白色方块电器）
    bw, bh = 520, 620
    d.rounded_rectangle([cx - bw // 2, cy - bh // 2, cx + bw // 2, cy + bh // 2], 30, fill=(246, 246, 250), outline=(140, 142, 148), width=3)
    # 前面板圆形视窗
    d.ellipse([cx - 170, cy - 90, cx + 170, cy + 250], outline=(90, 92, 98), width=4)
    d.ellipse([cx - 150, cy - 70, cx + 150, cy + 230], fill=(176, 182, 192))
    d.ellipse([cx - 110, cy - 30, cx + 110, cy + 190], fill=(40, 44, 52))
    # 旋钮
    d.ellipse([cx + 200, cy - 40, cx + 300, cy + 60], fill=(232, 232, 238), outline=(90, 92, 98), width=3)
    # 右下角撞破：碎洞 + 放射裂纹
    bx, by = cx + 150, cy + 210
    d.ellipse([bx - 40, by - 40, bx + 40, by + 40], fill=(120, 122, 128))
    for k in range(14):
        a3 = rnd.uniform(0, math.tau)
        d.line([bx, by, bx + math.cos(a3) * rnd.uniform(50, 130), by + math.sin(a3) * rnd.uniform(50, 130)], fill=(90, 92, 98), width=2)
    # 裂口填充
    d.polygon([(bx - 26, by - 34), (bx + 2, by - 6), (bx - 30, by + 12), (bx - 12, by - 2), (bx - 34, by - 8)], fill=(60, 62, 68))
    img.save(OUT_DIR / "broken_beam_washer.png")


# ---------------------------------------------------------------- 快递单
def gen_waybill() -> None:
    W, H = 900, 620
    img = Image.new("RGB", (W, H), (250, 250, 245))
    d = ImageDraw.Draw(img)
    f_title = font(FONT_HEI, 36)
    f_big = font(FONT_SONG, 30)
    f_mid = font(FONT_SONG, 26)
    f_small = font(FONT_SONG, 22)

    # 顶部红色品牌条（SF 顺丰风格）
    d.rectangle([0, 0, W, 92], fill=(205, 43, 42))
    d.text((30, 18), "顺丰速运   SF EXPRESS", font=f_title, fill=(255, 255, 255))
    d.text((W - 260, 30), "电子运单", font=f_big, fill=(255, 230, 200))

    # 物流单号
    track = "SF1378 0268 4521 9"
    d.text((60, 120), f"运单号码：{track}", font=f_big, fill=(20, 20, 20))

    # 寄件人 / 收件人
    d.text((60, 175), "寄件人：李国华   |   手机：138 0947 1526", font=f_mid, fill=(30, 30, 30))
    d.text((60, 215), "地址：广东省深圳市南山区科技园南路 88 号华能大厦 12 层 1203 室", font=f_mid, fill=(30, 30, 30))
    d.rectangle([40, 265, W - 40, 340], fill=(245, 245, 245), outline=(200, 200, 200))
    d.text((54, 280), "收件人：王小雨   |   手机：186 0512 9033", font=f_big, fill=(18, 18, 18))
    d.text((54, 320), "地址：浙江省杭州市西湖区文三路 478 号    收", font=f_mid, fill=(18, 18, 18))

    # 条形码（左侧） + 数字（下方）
    bx, by, bw2 = 60, 370, 460
    rnd = random.Random(31)
    x = bx
    while x < bx + bw2:
        w = rnd.choice([2, 2, 3, 4, 5])
        d.rectangle([x, by, x + w, by + 120], fill=(15, 15, 15))
        x += w + rnd.choice([2, 3])
    d.text((bx, by + 130), " 1 3 7  8 0 2 6  4 5 2 1  9 0 3 3  8", font=f_small, fill=(15, 15, 15))

    # 右侧寄件信息块（虚线框 + 物品/重量/件数）
    d.rectangle([W - 340, 440, W - 30, H - 30], outline=(150, 150, 150))
    d.text((W - 320, 450), "物品：移动电话", font=f_mid, fill=(30, 30, 30))
    d.text((W - 320, 486), "数量：1   重量：0.42 kg", font=f_mid, fill=(30, 30, 30))
    d.text((W - 320, 522), "保价：¥ 0.00", font=f_mid, fill=(30, 30, 30))

    # 左下角协议文字（小号灰字）
    d.text((56, 545), "请于签收前验货，本人开箱拍照，验货后再签收。如有破损请拒收或当场备注。", font=f_small, fill=(120, 120, 120))

    # 模拟折痕（浅色横线 x2）
    d.line([0, H - 92, W, H - 92], fill=(210, 210, 200), width=1)
    img.save(OUT_DIR / "express_waybill.png")


def gen_waybill_on_package() -> None:
    # 平铺快递盒 + 贴在上面的运单
    W, H = 1400, 1050
    img = Image.new("RGB", (W, H), (150, 142, 130))
    d = ImageDraw.Draw(img)
    rnd = random.Random(47)
    for _ in range(2400):
        x, y = rnd.randrange(W), rnd.randrange(H)
        img.putpixel((x, y), tuple(c + rnd.randint(-12, 12) for c in (150, 142, 130)))

    # 纸箱（牛皮纸色）
    cx, cy, bw, bh = W // 2, H // 2, 1080, 780
    d.rounded_rectangle([cx - bw // 2, cy - bh // 2, cx + bw // 2, cy + bh // 2], 14, fill=(196, 176, 138), outline=(120, 105, 78), width=4)
    # 纸箱瓦楞纹理
    for y in range(cy - bh // 2 + 20, cy + bh // 2, 18):
        d.line([cx - bw // 2 + 10, y, cx + bw // 2 - 10, y], fill=(186, 166, 128), width=1)
    # 顶部封口片 + 胶带
    d.rectangle([cx - bw // 2 + 40, cy - 36, cx + bw // 2 - 40, cy + 36], fill=(205, 193, 158))
    d.rectangle([cx - 260, cy - 36, cx + 260, cy + 36], fill=(178, 168, 150), outline=(150, 140, 120))
    # 提手孔
    d.ellipse([cx - 300, cy - 34, cx - 200, cy + 34], fill=(150, 142, 130), outline=(150, 140, 120))
    d.ellipse([cx + 200, cy - 34, cx + 300, cy + 34], fill=(150, 142, 130), outline=(150, 140, 120))

    # 贴上运单（复用简化版）
    wb = Image.new("RGB", (560, 380), (250, 250, 245))
    dw = ImageDraw.Draw(wb)
    dw.rectangle([0, 0, 560, 52], fill=(205, 43, 42))
    dw.text((16, 8), "顺丰速运", font=font(FONT_HEI, 32), fill=(255, 255, 255))
    dw.rectangle([0, 52, 560, 380], fill=(250, 250, 245), outline=(160, 160, 160))
    dw.text((20, 64), "SF1378 0268 4521 9", font=font(FONT_SONG, 26), fill=(20, 20, 20))
    dw.text((20, 104), "寄：李国华  深圳市南山区", font=font(FONT_SONG, 22), fill=(30, 30, 30))
    dw.text((20, 138), "收：王小雨  杭州市西湖区", font=font(FONT_SONG, 22), fill=(30, 30, 30))
    dw.rectangle([20, 190, 420, 280], outline=(20, 20, 20))
    bxx = 24
    while bxx < 420:
        www = rnd.choice([2, 3, 5])
        dw.rectangle([bxx, 196, bxx + www, 272], fill=(15, 15, 15))
        bxx += www + rnd.choice([2, 3])
    img.paste(wb, (cx - 280, cy + 230))

    # 运单四角贴纸
    img.save(OUT_DIR / "express_package.jpg")


# ---------------------------------------------------------------- 发票
def gen_invoice_goods() -> None:
    W, H = 1500, 1000
    img = Image.new("RGB", (W, H), (248, 248, 246))
    d = ImageDraw.Draw(img)
    rnd = random.Random(61)
    f_small = font(FONT_SONG, 22)
    f_mid = font(FONT_SONG, 26)
    f_big = font(FONT_HEI, 40)

    # 纸张底色 + 打印错位浅影（真实感）
    d.rectangle([6, 6, W - 6, H - 6], fill=(246, 246, 244), outline=(120, 120, 120))
    d.line([0, H - 90, W, H - 90], fill=(214, 214, 208), width=2)

    d.text((W // 2 - 320, 30), "增值税普通发票", font=f_big, fill=(20, 20, 20))
    d.text((W // 2 - 120, 90), "发票代码：13 024 203 180", font=f_mid, fill=(30, 30, 30))
    d.text((W // 2 - 120, 122), "发票号码：18874520", font=f_mid, fill=(30, 30, 30))
    d.text((W // 2 - 120, 154), "开票日期：2026 年 08 月 12 日", font=f_mid, fill=(30, 30, 30))

    # 购买方
    d.text((40, 200), "购买方信息：", font=f_mid, fill=(30, 30, 30))
    d.text((40, 236), "名称：杭州零便利电子商务有限公司", font=f_mid, fill=(30, 30, 30))
    d.text((40, 272), "纳税人识别号：91330100MA2B7K8Q4R", font=f_mid, fill=(30, 30, 30))

    # 表格
    tx0, ty0, tw = 40, 330, W - 80 - 80
    rows = [("货物或应税劳务名称", "规格型号", "单位", "数量", "单价", "金额", "税率", "税额"),
            ("移动电话  Xiaomi 13 (12+256)", "M13-256", "台", "1", "4999.00", "4999.00", "13%", "649.87")]
    col_w = [0.30, 0.10, 0.06, 0.08, 0.12, 0.12, 0.08, 0.11]
    d.rectangle([tx0, ty0, tx0 + tw, ty0 + 190], outline=(90, 90, 90), width=2)
    y = ty0
    for ri, row in enumerate(rows):
        x = tx0
        row_h = 60 if ri == 0 else 88
        for ci, cell in enumerate(row):
            wcol = int(tw * col_w[ci])
            if ri == 0:
                d.rectangle([x, y, x + wcol, y + row_h], outline=(90, 90, 90), width=1)
                d.text((x + 8, y + 18), cell, font=f_mid, fill=(20, 20, 20))
            else:
                d.rectangle([x, y, x + wcol, y + row_h], outline=(90, 90, 90), width=1)
                twx = x + (wcol - text_w(d, cell, f_mid)) / 2
                d.text((twx, y + 30), cell, font=f_mid, fill=(20, 20, 20))
            x += wcol
        y += row_h
    # 追加 2 行（黑色细字）
    for ci in range(8):
        x = tx0 + sum(int(tw * col_w[k]) for k in range(ci))
        d.rectangle([x, y, x + int(tw * col_w[ci]), y + 40], outline=(160, 160, 160), width=1)
    # 汇总行
    d.rectangle([tx0, y, tx0 + tw, y + 54], outline=(90, 90, 90), width=2)
    d.text((tx0 + 10, y + 12), "价税合计（大写）", font=f_mid, fill=(20, 20, 20))
    d.text((tx0 + tw - 420, y + 12), "¥ 5648.87", font=f_mid, fill=(20, 20, 20))

    # 销售方
    d.text((40, y + 80), "销售方：小米之家（深圳）官方直营店   纳税人识别号：91440300MA5F2Y3Q6T", font=f_small, fill=(40, 40, 40))
    # 盖章（红色圆形章）
    sx, sy, sr = 1240, y + 162, 92
    for k in range(36):
        a = k * math.tau / 36
        r0, r1 = sr - 8, sr
        bx2, by2 = sx + math.cos(a) * r0, sy + math.sin(a) * r0
        ex2, ey2 = sx + math.cos(a) * r1, sy + math.sin(a) * r1
        d.line([bx2, by2, ex2, ey2], fill=(205, 40, 45), width=3)
    d.ellipse([sx - 60, sy - 60, sx + 60, sy + 60], outline=(205, 40, 45), width=2)
    d.ellipse([sx - sr, sy - sr, sx + sr, sy + sr], outline=(205, 40, 45), width=4)
    d.text((sx - 34, sy - 14), "发票专用章", font=f_small, fill=(205, 40, 45))
    d.ellipse([sx - 15, sy + 14, sx + 16, sy + 44], outline=(205, 40, 45), width=2)

    img.save(OUT_DIR / "invoice_goods.png")


def gen_invoice_freight() -> None:
    W, H = 1500, 980
    img = Image.new("RGB", (W, H), (248, 248, 246))
    d = ImageDraw.Draw(img)
    f_small = font(FONT_SONG, 22)
    f_mid = font(FONT_SONG, 26)
    f_big = font(FONT_HEI, 38)

    d.rectangle([6, 6, W - 6, H - 6], fill=(246, 246, 244), outline=(120, 120, 120))
    d.text((W // 2 - 280, 30), "增值税普通发票", font=f_big, fill=(20, 20, 20))
    d.text((W // 2 - 120, 90), "发票代码：14 099 211 802", font=f_mid, fill=(30, 30, 30))
    d.text((W // 2 - 120, 122), "发票号码：90213257", font=f_mid, fill=(30, 30, 30))
    d.text((W // 2 - 120, 154), "开票日期：2026 年 08 月 15 日", font=f_mid, fill=(30, 30, 30))

    d.text((40, 200), "购买方：杭州零便利电子商务有限公司", font=f_mid, fill=(30, 30, 30))
    d.text((40, 236), "销售方：深圳市飞豹物流有限公司", font=f_mid, fill=(30, 30, 30))

    tx0, ty0, tw = 40, 310, W - 160
    rows = [("服务名称", "规格型号", "单位", "数量", "单价", "金额", "税率", "税额"),
            ("快递服务费", "SF 杭州-深圳 件", "件", "1", "18.00", "18.00", "6%", "1.08")]
    col_w = [0.30, 0.16, 0.06, 0.08, 0.12, 0.12, 0.08, 0.08]
    d.rectangle([tx0, ty0, tx0 + tw, ty0 + 150], outline=(90, 90, 90), width=2)
    y = ty0
    for ri, row in enumerate(rows):
        x = tx0
        row_h = 56 if ri == 0 else 70
        for ci, cell in enumerate(row):
            wcol = int(tw * col_w[ci])
            d.rectangle([x, y, x + wcol, y + row_h], outline=(150, 150, 150), width=1)
            if ri == 0:
                d.text((x + 8, y + 16), cell, font=f_mid, fill=(20, 20, 20))
            else:
                twx = x + (wcol - text_w(d, cell, f_mid)) / 2
                d.text((twx, y + 22), cell, font=f_mid, fill=(20, 20, 20))
            x += wcol
        y += row_h
    d.rectangle([tx0, y, tx0 + tw, y + 50], outline=(90, 90, 90), width=2)
    d.text((tx0 + 10, y + 10), "价税合计（大写）", font=f_mid, fill=(20, 20, 20))
    d.text((tx0 + tw - 320, y + 10), "¥ 19.08", font=f_mid, fill=(20, 20, 20))

    # 电子签名区域
    d.text((40, y + 80), "备注：电子发票，与纸质发票具有同等法律效力。", font=f_small, fill=(60, 60, 60))
    d.rectangle([40, y + 116, 420, y + 156], outline=(150, 150, 150))
    d.text((52, y + 124), "查验码：2 3 8 1 09 7 4", font=f_small, fill=(30, 30, 30))
    img.save(OUT_DIR / "invoice_freight.png")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gen_broken_screen_phone()
    gen_scratched_case()
    gen_broken_washer()
    gen_waybill()
    gen_waybill_on_package()
    gen_invoice_goods()
    gen_invoice_freight()
    total = 0
    print(f"已生成到 {OUT_DIR}")
    for p in sorted(OUT_DIR.glob("*")):
        sz = round(p.stat().st_size / 1024, 1)
        total += p.stat().st_size
        print(f"  {p.name:<28} {sz:>8} KB")
    print(f"总计 {round(total / 1024, 1)} KB，{len(list(OUT_DIR.glob('*')))} 个文件")


if __name__ == "__main__":
    main()