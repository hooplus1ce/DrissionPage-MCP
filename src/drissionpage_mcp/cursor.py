"""Windows 11 Dark HD 虚拟鼠标光标与 60FPS 平滑轨迹驱动模块。

特性：
1. 60 FPS 硬件加速：requestAnimationFrame + 三次缓动曲线（Cubic Ease-Out）插值移动；
2. 全局穿透无遮挡：挂载于顶层主文档 document.documentElement，fixed 定位 + z-index 2147483647 + pointer-events none；
3. 生命周期管理：操作时淡入，闲置 2.5s 平滑淡出，附带安全看门狗防止死锁；
4. 全局连贯坐标记忆：记录视口绝对坐标 (last_x, last_y)，多步交互连续滑行，杜绝从 (0, 0) 瞬移；
5. 环境变量全局控制：支持在根目录 .env 中配置 SHOW_CURSOR=true/false 开关。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# 优先加载项目根目录下的 .env
try:
    from dotenv import load_dotenv

    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_path.is_file():
        load_dotenv(dotenv_path=env_path, override=False)
    else:
        load_dotenv(override=False)
except Exception:
    pass

# Windows 11 Dark HD 高清光标图标 (32x32, 热点位于 x=5, y=10)
_CURSOR_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAB8klEQVR42u2WTUsCURSGy68srSZF6Z"
    "OIIiho1zJCw7XQOgjFH+BP0HLVbjYRtHFb0CzCHyC4aycJg7kRgtnoQnDET2Q6dzgTw6Bpee/QYg"
    "684L0jvM85Z+bcOzdnhRVW/OOYN8g8Y1EU/YVCIQC/XSgnyG4KyHA45BWMVqt1D1srIC9ogTnEYD"
    "C4JMY8z6si0Wg0nuFRELQKcjOFgOxviCn8VJVOp1WIer3+AutNEMcUwgigh6jVagJziFEApkLAO3"
    "A7CmAChIMaxE8AYyDWQIvUICYB6CHa7baYSCSOqEJMA0AUi8W+IeLx+DHs+ahATAugh2g2m2+w3j"
    "K8mOwB9O3I5XJXsF7Hien8cxV+C5BMJlUAQRCuYb2N05KMbBsTgFAopKRSKSWfzyvValU173a7Ej"
    "w7xDawASDGxFQLWZY/JEl6LRaLd9Fo9BT+s4vnhRdPT3otIBmT6Pf7cqlUeoxEImewTz6/AzTeAP"
    "lByzOfmNoo5jhONc9ms6p5pVJ5CofD57BHPrl97HcQZwAxXsLMZxvNnU7nghiScmslL5fLD/DoBP"
    "u8ozuaPZixdlmx0ZiGtl6vl4FWvIM+IfMMZryHpfahsUtnSv0e6MCSksESwIz9eDOie/iMqwKW1Y"
    "3ZenDMuky7FyKEHbN10OyxFcb4AvzesBnJB6WlAAAAAElFTkSuQmCC"
)

_OVERRIDE_ENABLED: bool | None = None


def is_cursor_enabled() -> bool:
    """检查是否全局启用鼠标光标可视化。"""
    global _OVERRIDE_ENABLED
    if _OVERRIDE_ENABLED is not None:
        return _OVERRIDE_ENABLED

    val = os.getenv("SHOW_CURSOR") or os.getenv("DRISSIONPAGE_SHOW_CURSOR")
    if val is None:
        return True  # 默认开启可视化光标，确保用户能够清晰观察自动化操作轨迹
    return val.strip().lower() not in {"0", "false", "no", "off", "disable", "disabled"}

def set_cursor_enabled(enabled: bool | None) -> None:
    """动态覆盖光标启用状态（传 None 恢复读取环境变量）。"""
    global _OVERRIDE_ENABLED
    _OVERRIDE_ENABLED = enabled


_CURSOR_HELPER_JS_TEMPLATE = r"""
(function () {
  var dataUrl = "__DATA_URL_PLACEHOLDER__";

  // 1. Windows 11 Dark HD 光标图元 (32x32，热点偏移 x=5, y=10)
  var cursor = document.getElementById('__dp_virtual_cursor__');
  if (!cursor) {
    cursor = document.createElement('div');
    cursor.id = '__dp_virtual_cursor__';
    cursor.style.cssText = [
      'position: fixed !important',
      'left: 0 !important',
      'top: 0 !important',
      'width: 32px !important',
      'height: 32px !important',
      'pointer-events: none !important',
      'z-index: 2147483647 !important',
      'background-image: url("' + dataUrl + '") !important',
      'background-size: 32px 32px !important',
      'background-repeat: no-repeat !important',
      'transform: translate3d(-100px, -100px, 0)',
      'will-change: transform, opacity',
      'opacity: 0',
      'transition: opacity 0.15s ease-out',
      'filter: drop-shadow(0 2px 5px rgba(0, 0, 0, 0.45)) !important'
    ].join('; ');
    document.documentElement.appendChild(cursor);
  } else {
    cursor.style.backgroundImage = 'url("' + dataUrl + '")';
  }
  var ripple = document.getElementById('__dp_virtual_ripple__');
  if (!ripple) {
    ripple = document.createElement('div');
    ripple.id = '__dp_virtual_ripple__';
    ripple.style.cssText = [
      'position: fixed !important',
      'left: 0 !important',
      'top: 0 !important',
      'width: 28px !important',
      'height: 28px !important',
      'border-radius: 50% !important',
      'border: 2px solid rgba(0, 120, 215, 0.75) !important',
      'background: rgba(0, 120, 215, 0.25) !important',
      'pointer-events: none !important',
      'z-index: 2147483646 !important',
      'transform: translate(-50%, -50%) scale(0)',
      'opacity: 0',
      'transition: transform 0.28s cubic-bezier(0.1, 0.8, 0.2, 1), opacity 0.28s ease-out'
    ].join('; ');
    document.documentElement.appendChild(ripple);
  }

  var hideTimer = null;
  var safetyTimer = null;
  var isDown = false;

  var renderPos = function (x, y) {
    window.__dp_last_x = x;
    window.__dp_last_y = y;
    var scale = isDown ? ' scale(0.92)' : ' scale(1)';
    cursor.style.transform = 'translate3d(' + (x - 5) + 'px, ' + (y - 10) + 'px, 0)' + scale;
    var ghost = document.getElementById('__dp_drag_ghost__');
    if (ghost) {
      ghost.style.transform = 'translate3d(' + (x + 14) + 'px, ' + (y + 14) + 'px, 0) rotate(2deg) scale(1.02)';
    }
  };

  var show = function (stayMs) {
    cursor.style.opacity = '1';
    var delay = stayMs || 2500;
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = setTimeout(function () {
      if (!isDown) cursor.style.opacity = '0';
    }, delay);

    if (safetyTimer) clearTimeout(safetyTimer);
    safetyTimer = setTimeout(function () {
      isDown = false;
      cursor.style.opacity = '0';
    }, Math.max(delay + 1000, 3500));
  };

  // 60 FPS 平滑滑行接口 (Promise 驱动，采用 Cubic Ease-Out 缓动)
  var activeGlideId = 0;
  window.__dp_cursor_glide = function (targetX, targetY, durationMs, easeType) {
    return new Promise(function (resolve) {
      var glideId = ++activeGlideId;
      show(3000);

      var startX = (window.__dp_last_x != null && window.__dp_last_x > 0)
        ? window.__dp_last_x
        : Math.max(0, targetX - 45);
      var startY = (window.__dp_last_y != null && window.__dp_last_y > 0)
        ? window.__dp_last_y
        : Math.max(0, targetY - 25);

      var dx = targetX - startX;
      var dy = targetY - startY;
      var dist = Math.hypot(dx, dy);
      var duration = (durationMs != null && durationMs > 0) ? durationMs : Math.min(350, Math.max(120, dist * 0.4));

      if (dist < 2 || duration <= 0) {
        renderPos(targetX, targetY);
        resolve({ finished: true });
        return;
      }

      var startTime = performance.now();
      function tick(now) {
        if (activeGlideId !== glideId) {
          resolve({ cancelled: true });
          return;
        }
        var elapsed = now - startTime;
        var progress = Math.min(elapsed / duration, 1);
        var ease = (easeType === 'linear') ? progress : (1 - Math.pow(1 - progress, 3)); // linear用于拖拽1:1同步，cubic用于自由移动
        renderPos(startX + dx * ease, startY + dy * ease);

        if (progress < 1) {
          requestAnimationFrame(tick);
        } else {
          renderPos(targetX, targetY);
          resolve({ finished: true });
        }
      }
      requestAnimationFrame(tick);
    });
  };

  // 动作反馈：按下、抬起、点击波纹
  window.__dp_cursor_act = function (act, x, y) {
    if (x != null && y != null) renderPos(x, y);
    show(2500);

    if (act === 'down') {
      isDown = true;
      if (window.__dp_last_x != null) renderPos(window.__dp_last_x, window.__dp_last_y);
    } else if (act === 'up') {
      isDown = false;
      if (window.__dp_last_x != null) renderPos(window.__dp_last_x, window.__dp_last_y);
    } else if (act === 'click') {
      var cx = (x != null) ? x : (window.__dp_last_x || 0);
      var cy = (y != null) ? y : (window.__dp_last_y || 0);
      ripple.style.transition = 'none';
      ripple.style.left = cx + 'px';
      ripple.style.top = cy + 'px';
      ripple.style.transform = 'translate(-50%, -50%) scale(0.3)';
      ripple.style.opacity = '1';
      requestAnimationFrame(function () {
        ripple.style.transition = 'transform 0.28s cubic-bezier(0.1, 0.8, 0.2, 1), opacity 0.28s ease-out';
        ripple.style.transform = 'translate(-50%, -50%) scale(1.6)';
        ripple.style.opacity = '0';
      });
    }
  };

  window.__dp_cursor_hide = function () {
    isDown = false;
    cursor.style.opacity = '0';
  };

  window.__dp_start_drag_ghost = function (text, color, iconSvg) {
    var g = document.getElementById('__dp_drag_ghost__');
    if (g) g.remove();
    g = document.createElement('div');
    g.id = '__dp_drag_ghost__';
    var theme = color || '#1890ff';
    g.style.cssText = [
      'position: fixed !important',
      'left: 0 !important',
      'top: 0 !important',
      'pointer-events: none !important',
      'z-index: 2147483645 !important',
      'background: rgba(255, 255, 255, 0.95) !important',
      'backdrop-filter: blur(6px) !important',
      'border: 2px solid ' + theme + ' !important',
      'box-shadow: 0 12px 32px rgba(0, 0, 0, 0.2), 0 2px 10px rgba(24, 144, 255, 0.3) !important',
      'border-radius: 6px !important',
      'padding: 8px 18px !important',
      'font-size: 13px !important',
      'font-weight: 600 !important',
      'font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif !important',
      'color: #1f1f1f !important',
      'display: flex !important',
      'align-items: center !important',
      'gap: 8px !important',
      'transform-origin: top left !important',
      'transition: opacity 0.2s ease-out, transform 0.05s linear !important',
      'opacity: 0 !important',
      'user-select: none !important'
    ].join('; ');

    var badge = '<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:' + theme + ';box-shadow:0 0 6px ' + theme + ';"></span>';
    g.innerHTML = (iconSvg || badge) + '<span>' + (text || '物料') + '</span>';
    document.documentElement.appendChild(g);

    var curX = window.__dp_last_x != null ? window.__dp_last_x : 100;
    var curY = window.__dp_last_y != null ? window.__dp_last_y : 100;
    g.style.transform = 'translate3d(' + (curX + 14) + 'px, ' + (curY + 14) + 'px, 0) rotate(2deg) scale(0.95)';

    requestAnimationFrame(function () {
      g.style.opacity = '1';
      g.style.transform = 'translate3d(' + (curX + 14) + 'px, ' + (curY + 14) + 'px, 0) rotate(2deg) scale(1.02)';
    });
  };

  window.__dp_stop_drag_ghost = function () {
    var g = document.getElementById('__dp_drag_ghost__');
    if (g) {
      g.style.transition = 'transform 0.25s cubic-bezier(0.1, 0.8, 0.2, 1), opacity 0.22s ease-out !important';
      g.style.opacity = '0';
      g.style.transform += ' scale(0.68)';
      setTimeout(function () { if (g && g.parentNode) g.remove(); }, 260);
    }
  };
  return 'installed';
})();
"""
CURSOR_HELPER_JS = _CURSOR_HELPER_JS_TEMPLATE.replace("__DATA_URL_PLACEHOLDER__", _CURSOR_DATA_URL)



def _resolve_top_tab(target: Any) -> Any:
    """提取顶层 Tab 对象（无论传入的是 Actions、ChromiumFrame 还是 Tab）。"""
    if target is None:
        return None
    if hasattr(target, "owner"):  # Actions 对象
        owner = target.owner
        return getattr(owner, "tab", owner)
    if hasattr(target, "tab") and getattr(target, "tab") is not None:  # ChromiumFrame
        return target.tab
    return target


def ensure_cursor_installed(target: Any) -> bool:
    """在顶层主文档安装虚拟光标。"""
    if not is_cursor_enabled():
        return False
    tab = _resolve_top_tab(target)
    if tab is None:
        return False
    try:
        tab.run_js(CURSOR_HELPER_JS)
        return True
    except Exception:
        return False


def glide_cursor(
    target: Any, target_x: float, target_y: float, duration_ms: int = 200, ease: str = "cubic"
) -> None:
    """以 60 FPS 平滑滑行至视口绝对坐标。支持 cubic（常规移动）与 linear（拖拽同步）。"""
    if not is_cursor_enabled():
        return
    tab = _resolve_top_tab(target)
    if tab is None:
        return
    try:
        ensure_cursor_installed(tab)
        tab.run_js(
            "if (window.__dp_cursor_glide) window.__dp_cursor_glide(arguments[0], arguments[1], arguments[2], arguments[3]);",
            target_x,
            target_y,
            duration_ms,
            ease,
        )
    except Exception:
        pass


def act_cursor(target: Any, action: str, x: float | None = None, y: float | None = None) -> None:
    """派发光标动作动效（down/up/click）。"""
    if not is_cursor_enabled():
        return
    tab = _resolve_top_tab(target)
    if tab is None:
        return
    try:
        ensure_cursor_installed(tab)
        tab.run_js(
            "if (window.__dp_cursor_act) window.__dp_cursor_act(arguments[0], arguments[1], arguments[2]);",
            action,
            x,
            y,
        )
    except Exception:
        pass

def update_cursor_pos(target: Any, x: float, y: float, down: bool = False, ripple: bool = False) -> None:
    """直接更新虚拟光标视口绝对坐标与按压状态。"""
    if not is_cursor_enabled():
        return
    tab = _resolve_top_tab(target)
    if tab is None:
        return
    try:
        ensure_cursor_installed(tab)
        down_str = "true" if down else "false"
        rip_str = "true" if ripple else "false"
        tab.run_js(
            f"if (window.__dp_cursor_update) window.__dp_cursor_update({float(x):.1f}, {float(y):.1f}, {down_str}, {rip_str});"
        )
    except Exception:
        pass

def start_drag_ghost(target: Any, text: str, color: str = "#1890ff") -> None:
    """启动跟随鼠标的拖拽物料幽灵卡片。"""
    if not is_cursor_enabled():
        return
    tab = _resolve_top_tab(target)
    if tab is None:
        return
    try:
        ensure_cursor_installed(tab)
        clean_text = text.replace("'", "").replace('"', "").replace("\n", " ")
        tab.run_js(
            f"if (window.__dp_start_drag_ghost) window.__dp_start_drag_ghost('{clean_text}', '{color}');"
        )
    except Exception:
        pass


def stop_drag_ghost(target: Any) -> None:
    """停止并淡出拖拽物料幽灵卡片。"""
    tab = _resolve_top_tab(target)
    if tab is None:
        return
    try:
        tab.run_js("if (window.__dp_stop_drag_ghost) window.__dp_stop_drag_ghost();")
    except Exception:
        pass


def hide_cursor(target: Any) -> None:
    """强制隐藏虚拟光标。"""
    tab = _resolve_top_tab(target)
    if tab is None:
        return
    try:
        tab.run_js("if (window.__dp_cursor_hide) window.__dp_cursor_hide();")
    except Exception:
        pass
