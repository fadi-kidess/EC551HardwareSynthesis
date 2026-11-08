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
		self.parent = out

	def __str__(self):
		return "\n".join(self._lines())

	# _lines was written by Claude, it visualizes a given tree
	def _lines(self, prefix="", is_last=True, is_root=True):
		# Label for this node: "out = op"
		label = f"{self.parent} ({self.op})"
		if is_root:
			lines = [label]
			child_prefix = ""
		else:
			lines = [prefix + ("└── " if is_last else "├── ") + label]
			child_prefix = prefix + ("    " if is_last else "│   ")

		for i, child in enumerate(self.children):
			last = (i == len(self.children) - 1)
			if isinstance(child, Node):
				lines.extend(child._lines(child_prefix, last, is_root=False))
			else:
				lines.append(child_prefix + ("└── " if last else "├── ") + str(child))
		return lines


def algo2tree(algorithm):
	node_dict = {}

	for step in algorithm:
		out, in1, op, in2 = step

		if in1 in node_dict.keys():
			in1 = node_dict[in1]
		if in2 in node_dict.keys():
			in2 = node_dict[in2]

		node = Node(out, op, in1, in2)
		node_dict[out] = node

	return node_dict


