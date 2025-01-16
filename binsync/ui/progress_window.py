import logging

import networkx as nx

from libbs.ui.qt_objects import (
    # QtWidgets
    QGraphicsScene,
    QGraphicsView,
    QGraphicsEllipseItem,
    QGraphicsTextItem,
    QGraphicsLineItem,
    QGraphicsItem,
    QLabel,
    QHBoxLayout,
    QVBoxLayout,
    QDialog,
    # QtGui
    QPen, QBrush, QColor, QPainter, QFont, QFontMetrics,
    # QtCore
    Qt, QLineF
)

_l = logging.getLogger(__name__)

try:
    from networkx.drawing.nx_agraph import graphviz_layout
    HAS_PYGRAPHVIZ = True
except ImportError:
    HAS_PYGRAPHVIZ = False
    _l.warning("pygraphviz not installed, progress view may be ugly.")



def color_from_hotness(hotness: float) -> QColor:
    """
    If hotness <= 0.0, node is white (255,255,255).
    Otherwise, map [0..1] from light pink -> red.
    """
    if hotness <= 0.0:
        return QColor(255, 255, 255)  # white
    h = min(1.0, hotness)
    r = 255
    g = int(200 * (1 - h))
    b = int(200 * (1 - h))
    return QColor(r, g, b)


class NodeItem(QGraphicsEllipseItem):
    """
    A node that:
      - Color depends on 'hotness'
      - Autosizes to fit its text at 'size=1' minimum
      - Then scales that size by the node's 'size' attribute in [1..10]
      - Only one node at a time can have a bold black outline
      - Clicking the node pans the view to center on it (camera movement)
      - Double-click prints "Node {label}" to the terminal
    """
    currently_selected_node = None

    def __init__(self, label_text, x, y,
                 hotness=0.0, size=5,  # size in [1..10]
                 parent=None,
                 view=None,
                 controller=None,
                 ):
        self.controller = controller
        # Step 1: measure text to find the base diameter for size=1
        font = QFont("Arial", 10)
        fm = QFontMetrics(font)
        text_width = fm.horizontalAdvance(label_text)
        text_height = fm.height()
        base_diameter = max(text_width + 20, text_height + 20)

        # Step 2: scale the diameter by 'size'
        # If size=1 => diameter=base_diameter (just big enough to fit text)
        # If size=10 => diameter=10x base_diameter
        diameter = base_diameter * size

        super().__init__(-diameter/2, -diameter/2, diameter, diameter, parent)
        self.radius = diameter / 2
        self.view = view
        self._edges = []
        self.setPos(x, y)

        # Pen styles
        self.default_pen = QPen(Qt.black, 2)
        self.highlight_pen = QPen(Qt.black, 5)
        self.setPen(self.default_pen)

        # Fill color
        fill_color = color_from_hotness(hotness)
        self.setBrush(QBrush(fill_color))

        # Create text item
        self.text_item = QGraphicsTextItem(label_text, self)
        self.text_item.setFont(font)
        self.text_item.setDefaultTextColor(Qt.black)
        text_bounds = self.text_item.boundingRect()
        # Center text
        self.text_item.setPos(-text_bounds.width()/2, -text_bounds.height()/2)

        # Movable, selectable
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )

    def add_edge(self, edge):
        self._edges.append(edge)

    def itemChange(self, change, value):
        # Update edges if the node moves
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            for edge in self._edges:
                edge.update_positions()
        return super().itemChange(change, value)

    def mouseDoubleClickEvent(self, event):
        label_text = self.text_item.toPlainText()
        print(f"Node {label_text} clicked")
        # Bold black outline for only one selected node
        if NodeItem.currently_selected_node and NodeItem.currently_selected_node is not self:
            NodeItem.currently_selected_node.setPen(NodeItem.currently_selected_node.default_pen)

        self.setPen(self.highlight_pen)
        NodeItem.currently_selected_node = self

        func_addr = label_text.split("_")[1]
        try:
            func_addr = int(func_addr, 16)
        except:
            func_addr = None

        if func_addr is not None:
            normalized_addr = func_addr - 0x400000
            self.controller.deci.gui_goto(normalized_addr)

        super().mouseDoubleClickEvent(event)


class EdgeItem(QGraphicsLineItem):
    """
    Connects two NodeItems. Lines stop at the node boundaries, not at centers.
    """
    def __init__(self, node1, node2, indirect=False):
        super().__init__()
        self.node1 = node1
        self.node2 = node2
        self.indirect = indirect

        pen = QPen(Qt.black, 2)
        if self.indirect:
            pen.setStyle(Qt.DashLine)
        self.setPen(pen)

        self.update_positions()

    def update_positions(self):
        line = QLineF(self.node1.scenePos(), self.node2.scenePos())
        uv = line.unitVector()

        # Shift start by node1's radius
        p1 = line.p1()
        p1.setX(p1.x() + uv.dx() * self.node1.radius)
        p1.setY(p1.y() + uv.dy() * self.node1.radius)

        # Shift end by node2's radius
        p2 = line.p2()
        p2.setX(p2.x() - uv.dx() * self.node2.radius)
        p2.setY(p2.y() - uv.dy() * self.node2.radius)

        self.setLine(QLineF(p1, p2))


class GraphView(QGraphicsView):
    """
    Allows panning (drag) and zooming (mouse wheel).
    """
    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setRenderHint(QPainter.Antialiasing)

    def wheelEvent(self, event):
        zoom_factor = 1.15
        if event.angleDelta().y() > 0:
            self.scale(zoom_factor, zoom_factor)
        else:
            self.scale(1 / zoom_factor, 1 / zoom_factor)


class GraphWindow(QDialog):
    """
    A QWidget that displays a NetworkX graph.
    """
    def __init__(self, G=None, completion=33, parent=None, controller=None):
        super().__init__(parent)
        self.setWindowTitle("Graph Viewer")
        self.controller = controller

        main_layout = QVBoxLayout(self)
        top_layout = QHBoxLayout()

        # Label left
        self.completion_label = QLabel(f"Completion: {completion}%")
        self.completion_label.setStyleSheet("color: white; font-size: 16px;")

        # Label right
        self.hotness_label = QLabel("Hotness: changes")
        self.hotness_label.setStyleSheet("color: red; font-size: 16px;")

        top_layout.addWidget(self.completion_label, alignment=Qt.AlignLeft)
        top_layout.addWidget(self.hotness_label, alignment=Qt.AlignRight)
        main_layout.addLayout(top_layout)

        # Create scene
        scene = QGraphicsScene(self)
        scene.setBackgroundBrush(QBrush(QColor("#333333")))

        # If no graph was provided, create a small sample
        # Each node has 'hotness' in [0..1], and 'size' in [1..10] (default=5).
        g = nx.Graph()
        # node => (hotness, size)
        # If size is omitted, that node will default to 5
        node_attributes = {
            "sub_42f4e1": {"size": 2, "hotness": 0.1},
            "sub_42f494": {"size": 3},
            "sub_42e67a": {"size": 7, "hotness": 0.5},
            "sub_42e37f": {"size": 3, "hotness": 0.1},
            "sub_42f430": {"size": 4, "hotness": 0.3},
            "sub_42f21a": {"size": 2, "hotness": 0.1}

        }
        # Default attributes
        default_attributes = {"size": 2, "hotness": 0.0}
        # Add nodes with attributes
        for node, attributes in node_attributes.items():
            # Combine default attributes with specific ones
            combined_attributes = {**default_attributes, **attributes}
            g.add_node(node, **combined_attributes)
        # Add edges
        edges = [
            ("start", "sub_42f4e1"),
            ("sub_42f4e1", "sub_42f494"),
            ("sub_42e67a", "sub_42f4e1"),
            ("sub_42e67a", "sub_42f430"),
            ("sub_42e67a", "sub_42e37f"),
            ("sub_42e67a", "sub_42e284"),
            ("sub_42e67a", "sub_468ffb"),
            ("sub_42e67a", "sub_468fd0"),
            ("sub_42e67a", "sub_42e4d3"),
            ("sub_42e67a", "sub_42f5af"),
            ("sub_42e67a", "sub_42e43f"),
            ("sub_42e67a", "sub_42e99c"),
            ("sub_42e67a", "sub_46744b"),
            ("sub_42e67a", "sub_42e4f0"),
            ("sub_42e67a", "sub_42f226"),
            ("sub_42f21a", "sub_42e67a"), # this should be indirect
            ("sub_42e37f", "sub_42ecb5"),
            ("sub_42e37f", "sub_433b7e"),
            ("sub_42e37f", "sub_468b77"),
            ("sub_42e37f", "sub_433bb0"),
        ]
        g.add_edges_from(edges)

        G = g
        # Ensure a "start" node if not present
        if "start" not in G:
            G.add_node("start", size=5, hotness=0.0)

        # Layout: graphviz "dot" if possible, else spring
        if HAS_PYGRAPHVIZ:
            pos = graphviz_layout(G, prog="dot")
        else:
            pos = nx.spring_layout(G, k=1.2, iterations=50)

        # Create a QGraphicsView
        view = GraphView(scene, self)

        # Build node items
        node_items = {}
        for node_name, data in G.nodes(data=True):
            hotness = data.get("hotness", 0.0)
            size_val = 1 #data.get("size", 1)  # default to 5 if missing
            x, y = pos[node_name]
            item = NodeItem(
                label_text=node_name,
                x=x,
                y=y,
                hotness=hotness,
                size=size_val,  # <--- scaled node
                view=view,
                controller=controller
            )
            scene.addItem(item)
            node_items[node_name] = item

        # Build edges
        for u, v, edata in G.edges(data=True):
            print(u)
            if str(u) == "sub_42f21a" or str(v) == "sub_42f21a":
                indirect = True
            else:
                indirect = edata.get("indirect", False)
            edge_item = EdgeItem(node_items[u], node_items[v], indirect)
            scene.addItem(edge_item)
            node_items[u].add_edge(edge_item)
            node_items[v].add_edge(edge_item)

        main_layout.addWidget(view)
        self.setStyleSheet("background-color: #222222;")
        self.resize(1000, 800)


def open_progress_window(controller):
    print("Opening progress window...")
    window = GraphWindow(completion=33, controller=controller)
    print("Progress window created. Shwoing now...")
    window.show()
    print("Progress window shown.")
    print("Window execing...")
    window.exec_()
    print("Window execed.")
