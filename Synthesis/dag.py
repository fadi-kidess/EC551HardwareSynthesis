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
import copy
from math import inf

class Node:
	def __init__(self, out, op, in1, in2):
		self.op = op
		self.children = [in1, in2]
		self.parents = []
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

		if in1 in node_dict.keys():
			in1 = node_dict[in1]
			d1 = in1.depth
		if in2 in node_dict.keys():
			in2 = node_dict[in2]
			d2 = in2.depth

		node = Node(out, op, in1, in2)
		node.depth = max(d1, d2) + 1

		if in1 in node_dict.values():
			in1.parents.append(node)
		if in2 in node_dict.values():
			in2.parents.append(node)

		node_dict[out] = node

	# Only keep root nodes
	for node in list(node_dict.values()):
		if node.children[1] in node_dict.values():
			del node_dict[node.children[1].name]
		if node.children[0] in node_dict.values():
			del node_dict[node.children[0].name]


	return node_dict

def find_upstream(node):
	list_nodes = [node]
	for parent in node.parents:
		list_nodes.extend(find_upstream(parent))
	return list_nodes	

def traverse(root):
	list_nodes = [root]
	if isinstance(root.children[0], Node):
		list_nodes.extend(traverse(root.children[0]))
	if isinstance(root.children[1], Node):
		list_nodes.extend(traverse(root.children[1]))
	return list_nodes
def collect_nodes(node_dict):
	seen, order = set(), []
	def visit(n):
		if id(n) in seen:
			return
		seen.add(id(n))
		for child in n.children:
			if isinstance(child, Node):
				visit(child)
		order.append(n)
	for root in node_dict.values():
		visit(root)
	return order
def schedule(node_dict, method="ASAP", max_op=1, max_ops=None, latency=None):
	if method == "ASAP":
		return ASAP(node_dict)
	elif method == "ALAP":
		return ALAP(node_dict)
	elif method == "CUSTOM":
		return CUSTOM(node_dict, max_ops=max_ops, latency=latency)
	elif method == "ASAP_CONST":
		return ASAP_const(node_dict, max_op)
	elif method == "ALAP_CONST":
		return ALAP_const(node_dict, max_op)
	else:
		raise ValueError(f"Unknown scheduling method: {method}")

def ASAP(node_dict):
	for node in node_dict.values():
		tree = traverse(node)
		for el in tree:
			el.step = el.depth
	return

def ASAP_const(node_dict, maxop_per_step):
	step_dict = {}

	for node in node_dict.values():
		tree = traverse(node)
		for el in tree:
			el.step = el.depth
			if el.step in step_dict.keys():
				if el not in step_dict[el.step]:
					step_dict[el.step].append(el)
			else:
				step_dict[el.step] = [el]

	column = sorted(step_dict.keys())[0]

	while column <= sorted(step_dict.keys())[-1]:
		# print("******************")
		# for step in sorted(step_dict.keys()):
		# 	print(f"\n Step: t{step}")
		# 	for st_node in step_dict[step]:
		# 		print(st_node.name)
				
		if column not in step_dict.keys():
			column += 1
			continue

		if len(step_dict[column]) > maxop_per_step:

			to_be_delayed = []
			for extra in step_dict[column][maxop_per_step:]:
				to_be_delayed.extend(find_upstream(extra))

			for node in list(set(to_be_delayed)):
				node.step += 1
				step_dict[node.step-1].remove(node)

				if (node.step) in step_dict.keys():
					step_dict[node.step].append(node)
				else:
					step_dict[node.step] = [node]

		column += 1	
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

def ALAP_const(node_dict, maxop_per_step):
	ASAP(node_dict)

	step_dict = {}
	min_step = inf

	for node in node_dict.values():
		tree = traverse(node)
		for el in tree:
			if el.step in step_dict.keys():
				if el not in step_dict[el.step]:
					step_dict[el.step].append(el)
			else:
				step_dict[el.step] = [el]

	column = sorted(step_dict.keys())[-1]

	while column >= sorted(step_dict.keys())[0]:
		# print("******************")
		# for step in sorted(step_dict.keys()):
		# 	print(f"\n Step: t{step}")
		# 	for st_node in step_dict[step]:
		# 		print(st_node.name)
				
		if column not in step_dict.keys():
			column -= 1
			continue

		if len(step_dict[column]) > maxop_per_step:

			to_be_hastened = []
			for extra in step_dict[column][maxop_per_step:]:
				to_be_hastened.extend(traverse(extra))
				# print(f"\tColumn {column}, node {extra.name}")
				# for name in to_be_hastened:
				# 	print("\t" + name.name)

			for node in list(set(to_be_hastened)):
				# print(f"Hastening: {node.name}, step: {node.step}")
				node.step -= 1
				min_step = min(min_step, node.step)

				step_dict[node.step+1].remove(node)
				if (node.step) in step_dict.keys():
					step_dict[node.step].append(node)
				else:
					step_dict[node.step] = [node]

		column -= 1

	all_nodes = []
	for node in node_dict.values():
		all_nodes.extend(traverse(node))

	for node in list(set(all_nodes)):
		node.step -= min_step - 1
	pass

def CUSTOM(node_dict, max_ops=None, latency=None):
	max_ops = dict(max_ops or {})
	latency = dict(latency or {})
	nodes = collect_nodes(node_dict)
 
	def lat(n):
		return latency.get(n.op, 1)
	def ready_delay(n):      # steps after issue before a consumer may issue
		return max(lat(n), 1)
 
	for op in {n.op for n in nodes}:
		if max_ops.get(op, inf) < 1:
			raise ValueError(f"max_ops['{op}'] must be >= 1: the program uses '{op}'")
 
	# Priority: longest path (in steps) from this op to the end of the schedule.
	# Reverse topological order guarantees every consumer is done first.
	in_graph = {id(n) for n in nodes}
	prio = {}
	for n in reversed(nodes):
		tail = max((prio[id(p)] for p in n.parents if id(p) in in_graph), default=0)
		prio[id(n)] = ready_delay(n) + tail
 
	unscheduled = set(id(n) for n in nodes)
	t = 1
	while unscheduled:
		candidates = []
		for n in nodes:
			if id(n) not in unscheduled:
				continue
			preds = [c for c in n.children if isinstance(c, Node)]
			if all(id(c) not in unscheduled and c.step + ready_delay(c) <= t for c in preds):
				candidates.append(n)
		candidates.sort(key=lambda n: (-prio[id(n)], n.depth, n.name))
 
		used = {}
		for n in candidates:
			k = used.get(n.op, 0)
			if k < max_ops.get(n.op, inf):
				n.step, n.unit, n.latency = t, k, lat(n)
				used[n.op] = k + 1
				unscheduled.discard(id(n))
		t += 1
	return node_dict
