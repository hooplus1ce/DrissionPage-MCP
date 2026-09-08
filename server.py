"""FastMCP 官方推荐的文件型入口：fastmcp.json / fastmcp run 指向此文件的 mcp 实例。"""

from drissionpage_mcp.server import mcp

if __name__ == "__main__":
    mcp.run()
