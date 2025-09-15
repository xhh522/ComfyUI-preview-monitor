"""
ComfyUI Preview Monitor Custom Nodes
===================================

多版本图像预览监控节点集合

包含节点:
- PreviewImageMonitor: Pygame版本 (稳定可靠)
- WebPreviewImageMonitorFixed: Web版本 (高性能Web界面)  
- HybridPreviewImageMonitor: 混合版本 (Pygame + Web浏览器)

功能特性:
- 多显示器支持
- 图像比较模式
- 缩放和平移
- 热键控制
- 性能优化

Author: AI Assistant
Version: 1.0.0
"""

import sys
import os

# 添加当前目录到Python路径
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# 初始化节点映射
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

# 加载Pygame版本 (始终可用)
try:
    from preview_monitor import NODE_CLASS_MAPPINGS as PYGAME_NODES, NODE_DISPLAY_NAME_MAPPINGS as PYGAME_NAMES
    NODE_CLASS_MAPPINGS.update(PYGAME_NODES)
    NODE_DISPLAY_NAME_MAPPINGS.update(PYGAME_NAMES)
    print("✅ Preview Monitor: Pygame版本加载成功")
except Exception as e:
    print(f"❌ Preview Monitor: Pygame版本加载失败: {e}")

# 加载Web版本
try:
    from web_preview_simple_fixed import NODE_CLASS_MAPPINGS as WEB_NODES, NODE_DISPLAY_NAME_MAPPINGS as WEB_NAMES
    NODE_CLASS_MAPPINGS.update(WEB_NODES)
    NODE_DISPLAY_NAME_MAPPINGS.update(WEB_NAMES)
    print("✅ Preview Monitor: Web版本加载成功")
except ImportError as e:
    print(f"⚠️ Preview Monitor: Web版本依赖缺失: {e}")
except Exception as e:
    print(f"❌ Preview Monitor: Web版本加载失败: {e}")

# 加载混合版本
try:
    from hybrid_preview_monitor import NODE_CLASS_MAPPINGS as HYBRID_NODES, NODE_DISPLAY_NAME_MAPPINGS as HYBRID_NAMES
    NODE_CLASS_MAPPINGS.update(HYBRID_NODES)
    NODE_DISPLAY_NAME_MAPPINGS.update(HYBRID_NAMES)
    print("✅ Preview Monitor: 混合版本加载成功")
except ImportError as e:
    print(f"⚠️ Preview Monitor: 混合版本依赖缺失: {e}")
except Exception as e:
    print(f"❌ Preview Monitor: 混合版本加载失败: {e}")

# 导出
__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS']

# 显示加载结果
print(f"🚀 ComfyUI Preview Monitor v1.0.0 加载完成")
print(f"📦 可用节点: {len(NODE_CLASS_MAPPINGS)} 个")
for name, display_name in NODE_DISPLAY_NAME_MAPPINGS.items():
    print(f"  • {display_name}")
print(f"✨ 支持多显示器、图像比较、缩放等功能")