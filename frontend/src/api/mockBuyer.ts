/** 买家侧数据层（v2.0 后端 customer_auth/products/cart/orders/cases 已落地）。
 *  本文件原是 mock 实现；现改为直连真实后端，但**保持原有导出的函数名与签名不变**，
 *  因此各买家页面除三个提交动作（登录/结算/售后）需 await 外，其余无需改动。
 *
 *  设计取舍（MVP）：
 *  - 商品只读本地 products.json（与后端 seed 的 392 条小米数据一致），不走网络。
 *  - 购物车/订单读取走「模块缓存 + 登录/变更时刷新」，使页面保持同步调用；
 *    变更类接口（加购/改数量/下单/售后）做网络请求，并对缓存做乐观更新。
 *  - 买家 JWT 单独存于 buyer_token，与员工 refund_token 隔离。
 */
import rawProducts from "./products.json";
import { clearBuyer } from "./client";

export interface Product {
  id: string;
  name: string;
  price: number; // 分
  stock: number;
  category: string;
  image: string; // 本地图片路径（public/products/images/）
  desc: string;
}

interface RawProduct {
  product_id: number;
  name: string;
  price_cents: number;
  category: string;
  description: string;
  image: string;
}

export interface CartItem {
  product_id: string;
  qty: number;
}

export interface OrderItem {
  product_id: string;
  name: string;
  price: number;
  qty: number;
}

export interface Order {
  id: string;
  items: OrderItem[];
  total: number;
  status: string; // 后端 8 态枚举映射为中文标签
  address: string; // v2.0 虚拟交易场景已不收地址，新订单为空串
  created_at: string;
}

export interface AfterSale {
  id: string;
  order_id: string;
  type: "退款" | "退货退款" | "换货";
  amount: number;
  reason: string;
  evidence: string | null;
  status: "待处理" | "处理中" | "已完结";
  /** 处理结果：后端真实判定（decision/review_reason）映射，如「退款成功」「已拒绝：…」 */
  result?: string | null;
  ticket_no: string | null;
  created_at: string;
}

interface BuyerAccount {
  phone: string;
  password: string;
  nickname: string;
}

/* ---------- 商品（小米真实数据，392 个 / 17 分类，本地只读） ---------- */

export const PRODUCTS: Product[] = (rawProducts as RawProduct[]).map((p) => ({
  id: String(p.product_id),
  name: p.name,
  price: p.price_cents,
  stock: (p.product_id % 50) + 5,
  category: p.category,
  image: `/products/${p.image}`,
  desc: p.description,
}));

export function getProduct(id: string): Product | undefined {
  return PRODUCTS.find((p) => p.id === id);
}

/* ---------- 买家 JWT（与员工 token 隔离） ---------- */

const BUYER_TOKEN_KEY = "buyer_token";

export function getBuyerToken(): string | null {
  return localStorage.getItem(BUYER_TOKEN_KEY);
}

async function buyerRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getBuyerToken();
  const headers: Record<string, string> = { ...(options.headers as Record<string, string>) };
  if (!(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;

  const resp = await fetch(`/api/v1${path}`, { ...options, headers });
  if (resp.status === 401 && path !== "/buyer/auth/login") {
    // 会话过期：清掉 JWT 与"已登录"标记并回登录页（与员工侧 client.ts 的 401 行为对齐），
    // 否则界面永远显示已登录、所有接口静默 401，支付/加购看起来"没反应"。
    // 登录接口自身的 401 是"账号或密码错误"，不能在此时误清理/跳转。
    localStorage.removeItem(BUYER_TOKEN_KEY);
    clearBuyer();
    window.location.href = "/buyer/login";
    throw new Error("登录已过期，请重新登录");
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error((data as { message?: string }).message ?? "请求失败");
  return data as T;
}

/* ---------- 缓存（让页面保持同步读取） ---------- */

let cartCache: CartItem[] = [];
let ordersCache: Order[] = [];

const STATUS_LABEL: Record<string, string> = {
  PENDING_PAYMENT: "待支付",
  PAID: "待发货",
  SHIPPED: "已发货",
  COMPLETED: "已完成",
  REFUNDING: "退款中",
  REFUNDED: "已退款",
  EXCHANGING: "换货中",
  CLOSED: "已关闭",
};

interface OrderResp {
  id: number;
  order_no: string;
  total_cents: number;
  status: string;
  created_at: string;
  items: { product_id: string; product_name: string; price_cents: number; quantity: number }[];
}

function mapOrder(o: OrderResp): Order {
  return {
    id: String(o.id),
    items: o.items.map((i) => ({
      product_id: i.product_id,
      name: i.product_name,
      price: i.price_cents,
      qty: i.quantity,
    })),
    total: o.total_cents,
    status: STATUS_LABEL[o.status] ?? o.status,
    address: "",
    created_at: o.created_at,
  };
}

interface CartRespItem {
  id: number;
  product_id: number;
  quantity: number;
}

async function refreshCart(): Promise<void> {
  try {
    const r = await buyerRequest<{ items: CartRespItem[] }>("/cart");
    cartCache = r.items.map((i) => ({ product_id: String(i.product_id), qty: i.quantity }));
  } catch {
    cartCache = [];
  }
}

export async function refreshOrders(): Promise<void> {
  try {
    const r = await buyerRequest<{ items: OrderResp[] }>("/orders");
    ordersCache = r.items.map(mapOrder);
  } catch {
    ordersCache = [];
  }
}

/* ---------- 买家账号（真实注册/登录，签发 BUYER JWT） ---------- */

interface AuthResp {
  access_token: string;
  phone: string;
  nickname: string | null;
}

export async function registerBuyer(
  phone: string,
  password: string,
  nickname: string,
): Promise<string | null> {
  try {
    const r = await buyerRequest<AuthResp>("/buyer/auth/register", {
      method: "POST",
      body: JSON.stringify({ phone, password, nickname: nickname || undefined }),
    });
    localStorage.setItem(BUYER_TOKEN_KEY, r.access_token);
    await refreshCart();
    await refreshOrders();
    return null;
  } catch (e) {
    return e instanceof Error ? e.message : "注册失败";
  }
}

export async function loginBuyer(phone: string, password: string): Promise<BuyerAccount | string> {
  try {
    const r = await buyerRequest<AuthResp>("/buyer/auth/login", {
      method: "POST",
      body: JSON.stringify({ phone, password }),
    });
    localStorage.setItem(BUYER_TOKEN_KEY, r.access_token);
    await refreshCart();
    await refreshOrders();
    return { phone: r.phone, password: "", nickname: r.nickname ?? `买家${phone.slice(-4)}` };
  } catch (e) {
    return e instanceof Error ? e.message : "手机号或密码错误";
  }
}

/* ---------- 购物车（真实接口 + 乐观更新缓存） ---------- */

export function getCart(_phone: string): CartItem[] {
  return cartCache;
}

export function cartCount(_phone: string): number {
  return cartCache.reduce((s, i) => s + i.qty, 0);
}

export function addToCart(_phone: string, productId: string, qty: number): void {
  const ex = cartCache.find((i) => i.product_id === productId);
  if (ex) ex.qty += qty;
  else cartCache.push({ product_id: productId, qty });
  buyerRequest("/cart", {
    method: "POST",
    body: JSON.stringify({ product_id: Number(productId), quantity: qty }),
  }).catch(() => {});
}

export function updateCartQty(_phone: string, productId: string, qty: number): void {
  if (qty <= 0) cartCache = cartCache.filter((i) => i.product_id !== productId);
  else {
    const ex = cartCache.find((i) => i.product_id === productId);
    if (ex) ex.qty = qty;
    else cartCache.push({ product_id: productId, qty });
  }
  // 解析购物车条目 id 后 PATCH/DELETE
  buyerRequest<{ items: CartRespItem[] }>("/cart")
    .then((cart) => {
      const it = cart.items.find((i) => String(i.product_id) === productId);
      if (!it) return;
      if (qty <= 0) buyerRequest(`/cart/${it.id}`, { method: "DELETE" }).catch(() => {});
      else
        buyerRequest(`/cart/${it.id}`, {
          method: "PATCH",
          body: JSON.stringify({ quantity: qty }),
        }).catch(() => {});
    })
    .catch(() => {});
}

export function clearCart(_phone: string): void {
  cartCache = [];
}

/* ---------- 订单（真实接口） ---------- */

export function getOrders(_phone: string): Order[] {
  return ordersCache;
}

export function getOrder(_phone: string, orderId: string): Order | undefined {
  return ordersCache.find((o) => o.id === orderId);
}

export async function createOrder(
  _phone: string,
  _items: OrderItem[],
  _address = "",
): Promise<Order> {
  // R1：以购物车为真相，后端按购物车条目下单后清空购物车
  const cart = await buyerRequest<{ items: CartRespItem[] }>("/cart");
  const ids = cart.items.map((i) => i.id);
  const order = await buyerRequest<OrderResp>("/orders", {
    method: "POST",
    body: JSON.stringify({ cart_item_ids: ids }),
  });
  const mapped = mapOrder(order);
  ordersCache = [mapped, ...ordersCache];
  cartCache = [];
  return mapped;
}

/** 模拟支付：调后端 POST /orders/{id}/pay 把订单置为 PAID（演示环境，无真实资金）。
 *  状态以后端为准，并替换缓存中该订单，订单页直接显示「待发货」而非前端靠 ?paid= 猜。 */
export async function payOrder(orderId: string): Promise<Order> {
  const resp = await buyerRequest<OrderResp>(`/orders/${orderId}/pay`, { method: "POST" });
  const mapped = mapOrder(resp);
  ordersCache = ordersCache.map((o) => (o.id === orderId ? mapped : o));
  return mapped;
}

/* ---------- 售后申请（真实建单 POST /api/v1/cases + 后端状态同步） ---------- */

const afterSaleKey = (phone: string) => `buyer_aftersale_${phone}`;

function loadLocalAfterSales(phone: string): AfterSale[] {
  return JSON.parse(localStorage.getItem(afterSaleKey(phone)) ?? "[]");
}

// 会话内缓存：我的售后/订单页共享一次后端同步结果，避免每次渲染重复拉取
let afterSaleCache: AfterSale[] | null = null;

/** 后端「我的售后」案件响应（GET /api/v1/buyer/cases）。 */
interface MyCaseResp {
  id: number;
  ticket_no: string;
  order_id: string;
  status: string;
  decision: string | null;
  review_reason: string | null;
  applicant_amount: number;
  actual_amount: number;
  description: string;
  /** 工单8 交叉校验：买家端显式三选一的售后类型（新案件来自后端；旧案件为 null）。 */
  claim_type: string | null;
  evidence_url: string | null;
  created_at: string;
}

/**
 * 案件描述 → 售后类型（旧案件无 claim_type 时的兜底）。
 * 新版 description 只放纯文本（不再拼类型前缀），类型以后端 claim_type 为准；
 * 该函数仅兼容旧案件「退款：原因」/「退货退款：原因」/「换货：原因」前缀格式。
 */
function inferAfterSaleType(desc: string): AfterSale["type"] {
  if (desc.startsWith("换货")) return "换货";
  if (desc.startsWith("退货退款")) return "退货退款";
  return "退款";
}

/** 案件八态 + 人工判定(decision/review_reason) -> 前端「待处理/处理中/已完结 + 处理结果」。
 *  KEY：REJECTED 案件最终会流转到 COMPLETED（关闭工单），所以"有没有被拒"只能看 decision。
 *  售后类型：优先用后端 claim_type（用户显式选择），旧案件无则按 description 前缀兜底。 */
function mapCaseToAfterSale(c: MyCaseResp): AfterSale {
  const type = (c.claim_type as AfterSale["type"]) || inferAfterSaleType(c.description);
  // 新案件 description 是纯文本原因（无「类型：」前缀），直接取全文；
  // 旧案件（无 claim_type）才按前缀「退款：原因」剥掉类型部分。
  const reason = c.claim_type ? c.description || "" : c.description.split("：")[1]?.trim() || c.description || "";
  let status: AfterSale["status"];
  let result: string;
  switch (c.status) {
    case "COMPLETED":
    case "REJECTED":
      status = "已完结";
      result =
        c.decision === "REJECT"
          ? `已拒绝${c.review_reason ? `：${c.review_reason}` : ""}`
          : type === "换货"
            ? "换货流程已完成"
            : "退款成功";
      break;
    case "REFUND_FAILED":
      status = "已完结";
      result = "退款失败：系统已自动重试，可联系客服";
      break;
    case "FAILED":
      status = "已完结";
      result = "退款失败：请重新发起或联系客服";
      break;
    case "SUSPENDED":
      status = "处理中";
      result = "人工复核中";
      break;
    case "APPROVED":
    case "REFUNDING":
      status = "处理中";
      result = "退款处理中";
      break;
    default: // CREATED / RUNNING
      status = "待处理";
      result = "系统审核中";
  }
  return {
    id: `C${c.id}`,
    order_id: c.order_id,
    type,
    amount: type === "换货" ? 0 : c.applicant_amount,
    reason,
    evidence: c.evidence_url,
    status,
    result,
    ticket_no: c.ticket_no,
    created_at: c.created_at,
  };
}

/** 合并本地镜像与后端同步：同一工单号（ticket_no）以后端判定为准，
 *  但保留本地录入的前端明细（类型/原因/凭证图）；后端多出的条目补进来，
 *  本地持有而后端暂无的条目（如刚提交）保留。 */
function mergeAfterSales(local: AfterSale[], backend: MyCaseResp[]): AfterSale[] {
  const localByTicket = new Map(
    local.filter((a) => a.ticket_no).map((a) => [a.ticket_no!, a]),
  );
  const remoteTickets = new Set<string>();
  const merged = backend.map((c) => {
    const r = mapCaseToAfterSale(c);
    if (!c.ticket_no) return r;
    remoteTickets.add(c.ticket_no);
    const li = localByTicket.get(c.ticket_no);
    if (!li) return r;
    return { ...li, ...r, type: li.type, reason: li.reason, evidence: li.evidence, id: li.id };
  });
  const extra = local.filter((a) => !a.ticket_no || !remoteTickets.has(a.ticket_no));
  return [...merged, ...extra];
}

/** 拉取后端真实案件状态并合并进本地镜像（「我的售后」以 PG 为真相）。
 *  会话过期/网络异常时静默保留本地镜像（401 由 buyerRequest 统一处理跳登录）。 */
export async function syncAfterSales(phone: string): Promise<void> {
  try {
    const r = await buyerRequest<{ items: MyCaseResp[] }>("/buyer/cases");
    const merged = mergeAfterSales(loadLocalAfterSales(phone), r.items);
    localStorage.setItem(afterSaleKey(phone), JSON.stringify(merged));
    afterSaleCache = merged;
  } catch {
    /* 保留本地镜像 */
  }
}

export function getAfterSales(phone: string): AfterSale[] {
  return afterSaleCache ?? loadLocalAfterSales(phone);
}

export async function createAfterSale(
  phone: string,
  orderId: string,
  type: AfterSale["type"],
  amount: number,
  reason: string,
  evidence: string | null = null,
  file: File | null = null,
): Promise<AfterSale> {
  // actual_amount 取订单实付额（后端 D-012：申报额不得超实付额）
  const order = ordersCache.find((o) => o.id === orderId);
  const actual = order ? order.total : amount;

  const fd = new FormData();
  fd.append("applicant_id", phone);
  fd.append("order_id", String(orderId));
  fd.append("applicant_amount", String(amount));
  fd.append("actual_amount", String(actual));
  // 工单8 交叉校验：售后类型以独立结构化字段提交（后端 create_case 落 claim_type，
  // 意图识别以用户显式选择为主通道）；description 只放纯文本，不再拼类型前缀——
  // 避免「退款：想换货」让规则层先命中「退款」跳过 LLM，导致换货诉求被吞掉。
  fd.append("claim_type", type);
  fd.append("description", reason);
  // 凭证图片真正上传（后端 create_case 接收 image 字段；此前 evidence 只存本地 base64，从未发到服务端）
  if (file) fd.append("image", file, file.name);

  const r = await buyerRequest<{ case_id: number; ticket_no: string; status: string }>(
    "/cases",
    { method: "POST", body: fd },
  );

  const sale: AfterSale = {
    id: `AS${Date.now()}`,
    order_id: String(orderId),
    type,
    amount,
    reason,
    evidence,
    status: "待处理",
    result: "系统审核中",
    ticket_no: r.ticket_no,
    created_at: new Date().toISOString(),
  };
  const list = loadLocalAfterSales(phone);
  list.unshift(sale);
  localStorage.setItem(afterSaleKey(phone), JSON.stringify(list));
  afterSaleCache = list;
  return sale;
}
