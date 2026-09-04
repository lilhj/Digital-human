/** 应用路由：
 *  运营侧（员工 JWT 守卫 + 六域侧边栏）：/ 工作台、/batch 批量审批、/eval 评测、/security 安全、/intent 意图、/telemetry 监控、/cases/:id 详情
 *  买家侧（/buyer/*）：商城、商品详情、购物车、结算、订单、售后申请/列表
 */
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { getToken } from "./api/client";
import OpsLayout from "./layouts/OpsLayout";
import BuyerLayout from "./layouts/BuyerLayout";
import LoginPage from "./pages/LoginPage";
import WorkbenchPage from "./pages/ops/WorkbenchPage";
import CaseDetailPage from "./pages/ops/CaseDetailPage";
import BatchApprovePage from "./pages/ops/BatchApprovePage";
import EvalPage from "./pages/ops/EvalPage";
import SecurityPage from "./pages/ops/SecurityPage";
import IntentPage from "./pages/ops/IntentPage";
import TelemetryPage from "./pages/ops/TelemetryPage";
import BuyerLoginPage from "./pages/buyer/BuyerLoginPage";
import ProductsPage from "./pages/buyer/ProductsPage";
import ProductDetailPage from "./pages/buyer/ProductDetailPage";
import CartPage from "./pages/buyer/CartPage";
import CheckoutPage from "./pages/buyer/CheckoutPage";
import OrdersPage from "./pages/buyer/OrdersPage";
import AfterSaleApplyPage from "./pages/buyer/AfterSaleApplyPage";
import AfterSaleListPage from "./pages/buyer/AfterSaleListPage";

function RequireAuth({ children }: { children: React.ReactNode }) {
  return getToken() ? <>{children}</> : <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* 员工登录 */}
        <Route path="/login" element={<LoginPage />} />

        {/* 运营侧（六域） */}
        <Route
          path="/"
          element={
            <RequireAuth>
              <OpsLayout />
            </RequireAuth>
          }
        >
          <Route index element={<WorkbenchPage />} />
          <Route path="batch" element={<BatchApprovePage />} />
          <Route path="eval" element={<EvalPage />} />
          <Route path="security" element={<SecurityPage />} />
          <Route path="intent" element={<IntentPage />} />
          <Route path="telemetry" element={<TelemetryPage />} />
          <Route path="cases/:id" element={<CaseDetailPage />} />
        </Route>

        {/* 买家侧 */}
        <Route path="/buyer/login" element={<BuyerLoginPage />} />
        <Route path="/buyer" element={<BuyerLayout />}>
          <Route index element={<ProductsPage />} />
          <Route path="product/:id" element={<ProductDetailPage />} />
          <Route path="cart" element={<CartPage />} />
          <Route path="checkout" element={<CheckoutPage />} />
          <Route path="orders" element={<OrdersPage />} />
          <Route path="orders/:id/after-sale" element={<AfterSaleApplyPage />} />
          <Route path="aftersale" element={<AfterSaleListPage />} />
          {/* 旧路径兼容（信息架构 §3 已统一为 orders/:id/after-sale） */}
          <Route path="aftersale/apply/:id" element={<Navigate to="/buyer/orders" replace />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
