"""AntV X6 页面端注入脚本。

包含：
- BIND_X6: 扫描 React Fiber 绑定 window.__x6_graph
- EXTRACT_GRAPH: 提取图模型完整拓扑（节点、边、连线关系、坐标与尺寸）
"""

# ---------------------------------------------------------------------------
# 扫描并绑定 X6 Graph 实例
# ---------------------------------------------------------------------------
BIND_X6 = r"""
var container = document.querySelector('.x6-graph');
if (!container) return JSON.stringify({ bound: false, reason: '未找到 .x6-graph 画布容器' });

if (window.__x6_graph && typeof window.__x6_graph.getNodes === 'function') {
    var rect = container.getBoundingClientRect();
    return JSON.stringify({
        bound: true,
        source: 'cached',
        zoom: window.__x6_graph.zoom(),
        translate: window.__x6_graph.translate(),
        containerRect: { x: rect.left, y: rect.top, width: rect.width, height: rect.height }
    });
}

// 检查全局 window 属性
for (var k in window) {
    try {
        if (window[k] && typeof window[k].getNodes === 'function' && typeof window[k].getEdges === 'function') {
            window.__x6_graph = window[k];
            var rect = container.getBoundingClientRect();
            return JSON.stringify({
                bound: true,
                source: 'window.' + k,
                zoom: window.__x6_graph.zoom(),
                translate: window.__x6_graph.translate(),
                containerRect: { x: rect.left, y: rect.top, width: rect.width, height: rect.height }
            });
        }
    } catch(e) {}
}

// 扫描 React Fiber 寻找 stateNode.graph
var fiberKey = Object.keys(container).find(function(k) { return k.startsWith('__reactInternalInstance$'); });
if (fiberKey) {
    var cur = container[fiberKey];
    for (var i = 0; i < 25 && cur; i++) {
        var inst = cur.stateNode;
        if (inst) {
            if (inst.graph && typeof inst.graph.getNodes === 'function') {
                window.__x6_graph = inst.graph;
                var rect = container.getBoundingClientRect();
                return JSON.stringify({
                    bound: true,
                    source: 'fiber:stateNode.graph@depth' + i,
                    zoom: window.__x6_graph.zoom(),
                    translate: window.__x6_graph.translate(),
                    containerRect: { x: rect.left, y: rect.top, width: rect.width, height: rect.height }
                });
            }
            for (var m in inst) {
                try {
                    if (inst[m] && typeof inst[m].getNodes === 'function' && typeof inst[m].getEdges === 'function') {
                        window.__x6_graph = inst[m];
                        var rect = container.getBoundingClientRect();
                        return JSON.stringify({
                            bound: true,
                            source: 'fiber:stateNode.' + m + '@depth' + i,
                            zoom: window.__x6_graph.zoom(),
                            translate: window.__x6_graph.translate(),
                            containerRect: { x: rect.left, y: rect.top, width: rect.width, height: rect.height }
                        });
                    }
                } catch(e2) {}
            }
        }
        cur = cur.return;
    }
}

// 兜底：DOM 节点存在即使无内部 Fiber 引用
var rect = container.getBoundingClientRect();
return JSON.stringify({
    bound: true,
    source: 'dom-only',
    zoom: 1,
    translate: { tx: 0, ty: 0 },
    containerRect: { x: rect.left, y: rect.top, width: rect.width, height: rect.height }
});
"""

# ---------------------------------------------------------------------------
# 提取图模型拓扑
# ---------------------------------------------------------------------------
EXTRACT_GRAPH = r"""
var g = window.__x6_graph;
var container = document.querySelector('.x6-graph');
if (!container) return JSON.stringify({ ok: false, reason: '未找到 .x6-graph' });
var cRect = container.getBoundingClientRect();

var nodes = [];
var edges = [];
var zoom = 1;
var translate = { tx: 0, ty: 0 };

if (g && typeof g.getNodes === 'function') {
    zoom = g.zoom();
    translate = g.translate();
    // 省 token：节点业务数据值级截断（键结构保留，字符串值 ≤200 字符），
    // 拓扑断言（节点/边/端口/坐标）不受影响；长配置走节点配置弹窗读取
    var cap = function (v) {
        if (v === null || v === undefined) return v;
        if (typeof v === 'string') return v.length > 200 ? v.slice(0, 200) : v;
        if (typeof v === 'number' || typeof v === 'boolean') return v;
        if (Array.isArray(v)) return v.map(cap);
        if (typeof v === 'object') {
            var o = {};
            for (var k in v) { try { o[k] = cap(v[k]); } catch (e) {} }
            return o;
        }
        return String(v).slice(0, 200);
    };
    nodes = g.getNodes().map(function(n) {
        var pos = n.getPosition ? n.getPosition() : { x: 0, y: 0 };
        var size = n.getSize ? n.getSize() : { width: 0, height: 0 };
        var data = n.getData ? n.getData() : (n.data || {});
        var ports = n.getPorts ? n.getPorts() : [];
        return {
            id: n.id,
            shape: n.shape,
            position: pos,
            size: size,
            data: cap(data),
            ports: ports
        };
    });
    edges = g.getEdges().map(function(e) {
        return {
            id: e.id,
            source: e.getSourceCellId ? e.getSourceCellId() : e.source,
            target: e.getTargetCellId ? e.getTargetCellId() : e.target,
            sourcePort: e.getSourcePortId ? e.getSourcePortId() : null,
            targetPort: e.getTargetPortId ? e.getTargetPortId() : null
        };
    });
}

return JSON.stringify({
    ok: true,
    zoom: zoom,
    translate: translate,
    containerRect: { x: cRect.left, y: cRect.top, width: cRect.width, height: cRect.height },
    nodes: nodes,
    edges: edges
});
"""

X6_SCRIPTS = {
    "bind": BIND_X6,
    "extract": EXTRACT_GRAPH,
}
