"""
Given an ssa.txt such as

a1 = x + y 
b = a1 + y 
a2 = b + c 
X1 = A + B 
X2 = A + C 
Y = X2 + A 
Z = X2 - B 
Q = B * Z 

Generate a tree where nodes are operators, edges are variables
"""

class Node:
	def __init__(self, out, op, in1, in2):
		self.op = op
		self.children = [in1, in2]
		self.name = out
		self.step = 0
		self.depth = 0

	# Claude wrote __str__ and _draw for tree visualization
	def __str__(self, width=10):
		rows = []
		self._draw(rows, width)
		header = f"depth={self.depth}\n" + "in".ljust(width) + "".join(f"t={s}".ljust(width) for s in range(1, self.step + 1))
		return "\n".join([header] + ["".join(r).rstrip() for r in rows])

	def _draw(self, rows, width):
		"""Appends this subtree's rows; returns (row, end column) of this node's label."""
		bx = self.step * width - 2                      # column of the edge bar into this node
		ends = []
		for child in self.children:
			if isinstance(child, Node):
				ends.append(child._draw(rows, width))
			else:
				rows.append(list(str(child)))
				ends.append((len(rows) - 1, len(str(child))))

		label = f"{self.op} ({self.name})"
		rows.append(list(" " * bx + "└ " + label))
		r = len(rows) - 1

		# vertical bar from the first child down to this node
		for rr in range(ends[0][0], r):
			rows[rr] += [" "] * (bx + 1 - len(rows[rr]))
			rows[rr][bx] = "│"
		# horizontal edge from each child to the bar
		for i, (cr, end) in enumerate(ends):
			rows[cr][end + 1:bx] = "─" * (bx - end - 1)
			rows[cr][bx] = "┐" if i == 0 else "┤"

		return r, bx + 2 + len(label)


def algo2tree(algorithm):
	node_dict = {}

	for step in algorithm:
		out, in1, op, in2 = step

		d1, d2 = 0, 0

		depends = False
		if in1 in node_dict.keys():
			in1 = node_dict[in1]
			d1 = in1.depth
		if in2 in node_dict.keys():
			in2 = node_dict[in2]
			d2 = in2.depth

		node = Node(out, op, in1, in2)
		node.depth = max(d1, d2) + 1

		node_dict[out] = node

	for node in list(node_dict.values()):
		if node.children[1] in node_dict.values():
			del node_dict[node.children[1].name]
		if node.children[0] in node_dict.values():
			del node_dict[node.children[0].name]

	return node_dict

def traverse(root):
	list_nodes = [root]
	if isinstance(root.children[0], Node):
		list_nodes.extend(traverse(root.children[0]))
	if isinstance(root.children[1], Node):
		list_nodes.extend(traverse(root.children[1]))
	return list_nodes

def schedule(node_dict, method="ASAP"):
	if method == "ASAP":
		return ASAP(node_dict)
	elif method == "ALAP":
		return ALAP(node_dict)
	elif method == "CUSTOM":
		return CUSTOM(node_dict)
	elif method == "ASAP_CONST":
		return ASAP_const(node_dict)
	elif method == "ALAP_CONST":
		return ALAP_const(node_dict)
	else:
		raise ValueError(f"Unknown scheduling method: {method}")

def ASAP(node_dict):
	for node in node_dict.values():
		tree = traverse(node)
		for el in tree:
			el.step = el.depth
	return

def ASAP_const(node_dict):
	pass

def ALAP(node_dict):
	t_max = 0
	for node in node_dict.values():
		t_max = max(t_max, node.depth)

	for node in node_dict.values():
		node.step = t_max

	current_nodes = node_dict.values()
	for step in range(t_max):
		if len(current_nodes) <= 0:
			break

		needed = []
		for node in current_nodes:
			if isinstance(node.children[0], Node):
				needed.append(node.children[0])
				needed[-1].step = node.step - 1
			if isinstance(node.children[1], Node):
				needed.append(node.children[1])
				needed[-1].step = node.step - 1

		current_nodes = needed

	return node_dict

def ALAP_const(node_dict):
	pass

def CUSTOM(node_dict):
	pass