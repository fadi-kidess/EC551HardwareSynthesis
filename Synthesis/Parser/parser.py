
import copy

def parse(text):
    algorithm = []
    for line in text:
        # A step in the algorithm is the output, an input, an operation, and the second input.
        step = ['','','', '']
        out,after = line.split('=')[0].strip(), line.split('=')[1].strip()
        step[0] = out
        op = ''
        if('*' in after):
            op = '*'
        elif ('+' in after):
            op = '+'
        else:
            op = '-'
        step[1], step[3] = after.split(op)[0].strip(), after.split(op)[1].strip()
        step[2] = op
        algorithm.append(step)
    return algorithm

def ssa(algo):
    ssa_algo = copy.deepcopy(algo)
    outs = [i[0] for i in algo]
    outs_set = list(set(outs))
    outs_count = [outs.count(out) for out in outs_set]
    outs_current = [0 for out in outs_set]
    for i in range(len(ssa_algo)):
        element_idx = outs_set.index(ssa_algo[i][0])
        if(outs_count[element_idx] > 1):
            outs_current[element_idx] = outs_current[element_idx] + 1
            ssa_algo[i][0] = f'{ssa_algo[i][0]}{outs_current[element_idx]}'
        if(ssa_algo[i][1] in outs_set):
            element_idx = outs_set.index(ssa_algo[i][1])
            if(outs_count[element_idx] > 1):
                ssa_algo[i][1] = f'{ssa_algo[i][1]}{outs_current[element_idx]}'
        if(ssa_algo[i][3] in outs_set):
            element_idx = outs_set.index(ssa_algo[i][3])
            if(outs_count[element_idx] > 1):
                ssa_algo[i][3] = f'{ssa_algo[i][3]}{outs_current[element_idx]}'
    return ssa_algo
def export_ssa(algo):
    txt = ''
    for i in algo:
        for enum,j in enumerate(i):
            txt += j + ' '
            if(enum == 0):
                txt += '= '
        txt += '\n'
    return txt

