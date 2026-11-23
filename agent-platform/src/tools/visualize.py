"""Data visualization tool."""

import asyncio
import base64
import io
import json
from threading import RLock
from typing import Any

import matplotlib
import pandas as pd

from .base import BaseTool, registry

# Use non-interactive backend
matplotlib.use("Agg")
import matplotlib.pyplot as plt

_PLOT_LOCK = RLock()


class VisualizationTool(BaseTool):
    """Create data visualizations."""

    name = "visualize"
    description = "Create charts and graphs from data"
    parameters = {
        "type": "object",
        "properties": {
            "data": {
                "type": "string",
                "description": "JSON array of objects or CSV string",
            },
            "chart_type": {
                "type": "string",
                "description": "Chart type: bar, line, scatter, pie, histogram",
                "enum": ["bar", "line", "scatter", "pie", "histogram"],
            },
            "x_column": {
                "type": "string",
                "description": "Column name for x-axis",
            },
            "y_column": {
                "type": "string",
                "description": "Column name for y-axis (for bar/line/scatter)",
            },
            "title": {
                "type": "string",
                "description": "Chart title",
                "default": "",
            },
        },
        "required": ["data", "chart_type"],
    }

    def _parse_data(self, data: str) -> pd.DataFrame:
        """Parse data string to DataFrame."""
        try:
            # Try JSON first
            parsed = json.loads(data)
            if isinstance(parsed, list):
                return pd.DataFrame(parsed)
            elif isinstance(parsed, dict):
                return pd.DataFrame([parsed])
        except json.JSONDecodeError:
            pass

        # Try CSV
        try:
            return pd.read_csv(io.StringIO(data))
        except Exception:
            pass

        raise ValueError("Data must be valid JSON array or CSV")

    async def execute(self, data, chart_type, x_column=None, y_column=None, title=""):
        return await asyncio.to_thread(self._execute, data, chart_type, x_column, y_column, title)

    def _execute(self, *args):
        # Matplotlib 的全局状态不支持并发渲染。
        with _PLOT_LOCK:
            try:
                return self._render(*args)
            finally:
                plt.close("all")

    def _render(
        self,
        data: str,
        chart_type: str,
        x_column: str = None,
        y_column: str = None,
        title: str = "",
    ) -> dict[str, Any]:
        """Create visualization."""
        try:
            if chart_type not in {"bar", "line", "scatter", "pie", "histogram"}:
                raise ValueError("Unsupported chart type")
            # Parse data
            df = self._parse_data(data)

            # Create figure
            plt.figure(figsize=(10, 6))

            # Create chart based on type
            if chart_type == "bar":
                if y_column:
                    df.plot.bar(x=x_column, y=y_column)
                else:
                    df.plot.bar()

            elif chart_type == "line":
                if y_column:
                    df.plot.line(x=x_column, y=y_column)
                else:
                    df.plot.line()

            elif chart_type == "scatter":
                if x_column and y_column:
                    df.plot.scatter(x=x_column, y=y_column)
                else:
                    return {
                        "success": False,
                        "error": "Scatter plot requires x_column and y_column",
                    }

            elif chart_type == "pie":
                if x_column and y_column:
                    df.set_index(x_column)[y_column].plot.pie()
                else:
                    return {
                        "success": False,
                        "error": "Pie chart requires x_column (labels) and y_column (values)",
                    }

            elif chart_type == "histogram":
                if y_column:
                    df[y_column].plot.hist()
                else:
                    df.plot.hist()

            # Set title
            if title:
                plt.title(title)

            plt.tight_layout()

            # Save to base64
            buffer = io.BytesIO()
            plt.savefig(buffer, format="png", dpi=100)
            buffer.seek(0)
            image_base64 = base64.b64encode(buffer.read()).decode()
            plt.close()

            return {
                "success": True,
                "chart_type": chart_type,
                "image_base64": image_base64,
                "rows": len(df),
                "columns": list(df.columns),
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }


def init_visualization_tool():
    """Initialize visualization tool."""
    registry.register(VisualizationTool())
