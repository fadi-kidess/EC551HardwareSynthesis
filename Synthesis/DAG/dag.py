from collections import defaultdict, deque
class DAG:
    def __init__(self):
        # Maps a parent node to a set of child nodes
        self.graph = {}
        self.op = {}
        self.slots = []
        self.input_list = []
        
    def add_node(self, node):
        if node not in self.graph:
            self.graph[node] = set()
    def add_edge(self, a, b, c,op):
        """Adds a directed edge from node u to node v."""
        self.add_node(a)
        self.add_node(b)
        self.add_node(c)
        
        # Add edge temporarily to check for cycles
        self.graph[b].add(a)
        self.op.setdefault(a, set()).add(op)
        
        if self._has_cycle():
            self.graph[b].remove(a)
            raise ValueError(f"Adding edge ({a} -> {b}) would create a cycle!")
        self.graph[c].add(a)
        if self._has_cycle():
            self.graph[c].remove(a)
            raise ValueError(f"Adding edge ({a} -> {c}) would create a cycle!")
        
    def _has_cycle(self):
        """Internal helper to detect cycles using DFS coloring."""
        visited = {}  # 0: unvisited, 1: visiting, 2: visited
        
        def dfs(node):
            visited[node] = 1  # Mark as visiting (gray)
            for neighbor in self.graph.get(node, []):
                if visited.get(neighbor, 0) == 1:
                    return True
                if visited.get(neighbor, 0) == 0:
                    if dfs(neighbor):
                        return True
            visited[node] = 2  # Mark as completely visited (black)
            return False

        for node in self.graph:
            if visited.get(node, 0) == 0:
                if dfs(node):
                    return True
        return False

    def find_roots(self):
        """Finds and returns all root nodes (indegree == 0)."""
        # Collect all nodes that have incoming edges (children)
        all_children = set()
        for children in self.graph.values():
            all_children.update(children)
            
        # Roots are nodes that are keys in our graph but never show up as children
        roots = [node for node in self.graph if node not in all_children]
        return roots
    def max_len(self):
        # Calculate indegrees
        indegree = {node: 0 for node in self.graph}

        for node, children in self.graph.items():
            for child in children:
                indegree[child] += 1

        # Initialize roots
        queue = deque(
            node for node in self.graph
            if indegree[node] == 0
        )

        # Longest-path levels
        levels = {node: 0 for node in self.graph}

        # Topological traversal
        while queue:
            node = queue.popleft()

            for child in self.graph[node]:

                levels[child] = max(
                    levels[child],
                    levels[node] + 1
                )

                indegree[child] -= 1

                if indegree[child] == 0:
                    queue.append(child)

        # Group nodes by level
        datapath = defaultdict(list)

        for node, level in levels.items():
            datapath[level].append(node)

        # Identify leaves
        leaves = [
            node for node in self.graph
            if not self.graph[node]
        ]

        return dict(datapath), levels, leaves
    def calculate_alap(self):

        # Calculate indegrees
        indegree = {node: 0 for node in self.graph}

        for node, children in self.graph.items():
            for child in children:
                indegree[child] += 1

        # Topological sorting
        queue = deque(
            node for node in self.graph
            if indegree[node] == 0
        )

        topo = []

        while queue:
            node = queue.popleft()
            topo.append(node)

            for child in self.graph[node]:
                indegree[child] -= 1

                if indegree[child] == 0:
                    queue.append(child)

        if len(topo) != len(self.graph):
            raise ValueError("Graph contains a cycle")

        # Calculate ASAP / longest-path levels
        asap = {node: 0 for node in self.graph}

        for node in topo:
            for child in self.graph[node]:
                asap[child] = max(
                    asap[child],
                    asap[node] + 1
                )

        # Determine schedule length
        max_time = max(asap.values()) + 1

        # Calculate ALAP in reverse topological order
        alap = {}

        for node in reversed(topo):

            successors = self.graph[node]

            if not successors:
                alap[node] = max_time - 1
            else:
                alap[node] = min(
                    alap[child] - 1
                    for child in successors
                )

        return asap, alap, max_time
    def old_max_len(self):
        roots = self.find_roots()
        visited = set()
        max_len = []
        datapath = []
        op_path = []
        leaf = []
        def dfs_walk(node,c_len):
            
            current_len  = c_len
            #if node in visited:
            #    return
            #visited.add(node)
            
            # Execute the callback function on the node
            # Recurse for all child nodes
            for child in self.graph.get(node, []):
                dfs_walk(child,current_len+1)
            max_len.append(current_len)
            print(node)
            print(max_len)
            while(len(datapath) < current_len + 1):
                datapath.append([])
            datapath[current_len].append(node)
            if(not self.graph[node]):
                
                self.input_list.append(node)
                if (node not in leaf):
                    leaf.append(node)
                print(f'{node} is a leaf!')
            else:
                while(len(op_path) < current_len + 1):
                    op_path.append([])
                op_path[current_len].append(node)
        # Start traversal from every root node found
        print(roots)
        for root in roots:
            max_len = []
            dfs_walk(root,0)
            print(datapath)
            seen = set()
            op_path = [[i for i in reversed(sub) if not (i in seen or seen.add(i))][::-1] for sub in reversed(op_path)][::-1]
            print(op_path)
            ops = [[self.op[c] for c in cycle] for cycle in op_path]
            print(ops)
    def traverse(self, visitor_callback):
        """Finds all roots and starts a DFS traversal from them.
        
        Ensures each node is only visited once, even if reached by multiple roots.
        """
        roots = self.find_roots()
        visited = set()

        def dfs_walk(node):
            if node in visited:
                return
            visited.add(node)
            
            # Execute the callback function on the node
            visitor_callback(node)
            
            # Recurse for all child nodes
            for child in self.graph.get(node, []):
                dfs_walk(child)

        # Start traversal from every root node found
        for root in roots:
            dfs_walk(root)
    def asap(self):
        roots = self.find_roots()
        visited = set()

        def dfs_walk(node):
            if node in visited:
                return
            visited.add(node)
            
            # Execute the callback function on the node
            visitor_callback(node)
            
            # Recurse for all child nodes
            for child in self.graph.get(node, []):
                dfs_walk(child)

        # Start traversal from every root node found
        for root in roots:
            dfs_walk(root)
def create_dag_from_ssa(ssa_algo):
    dag = DAG()
    for i in ssa_algo:
        dag.add_edge(i[0],i[1],i[3],i[2])
    
    
    return dag
    
    
# ==========================================
# Example Usage:
# ==========================================
if __name__ == "__main__":
    d = DAG()
    
    # Constructing a DAG with two roots: 'A' and 'E'
    # Both paths eventually converge at 'D'
    d.add_edge("A", "X","Y","+")
    d.add_edge("B", "A","Y","+")
    #d.add_edge("A", "C")
    #d.add_edge("B", "D")
    #d.add_edge("C", "D")
    #d.add_edge("E", "D")

    # 1. Identify roots
    print(f"Roots found: {d.find_roots()}") 
    # Output: ['A', 'E']
    
    # 2. Traverse from those roots
    print("\n--- Starting Traversal ---")
    d.traverse(lambda node: print(f"Processing node: {node}"))
    print(d.graph)
    print(d.op)

