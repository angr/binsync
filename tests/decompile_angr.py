import angr
import sys
import subprocess
import os
import toml


def parse_binary(binary_path, output_path):
    p = angr.Project(binary_path, load_options={"auto_load_libs": False})
    cfg = p.analyses.CFG(data_references=True, normalize=True)
    p.analyses[angr.analyses.CompleteCallingConventionsAnalysis].prep()(recover_variables=True, analyze_callsites=True)

    # binary info
    binary_info = {}
    binary_info["binary"] = os.path.basename(binary_path)
    binary_info["md5"] = subprocess.check_output(["md5sum", binary_path]).decode().split()[0]
    binary_info["functions"] = {}
    binary_info["globals"] = {}

    # functions
    for addr, func in cfg.functions.items():
        if func.is_plt or func.is_simprocedure or func.alignment:
            continue
        function = {"name": func.name, "variables": [], "arguments": []}
        try:
            decomp = p.analyses.Decompiler(func, cfg=cfg.model)
        except Exception as e:
            print(f"Decompilation Error @ {hex(addr)}: {e}")
            continue
        if not decomp.codegen:
            continue
        for extern in decomp.codegen.cexterns:
            if extern.variable_type:
                binary_info["globals"][str(extern.variable.addr)] = {"name": extern.variable.name, "size": extern.variable_type.size // 8, "type": extern.variable_type.c_repr()}
            else:
                binary_info["globals"][str(extern.variable.addr)] = {"name": extern.variable.name}
        function['return_type'] = decomp.codegen.cfunc.functy.returnty.c_repr()
        for idx, (arg_type, arg_cvar) in enumerate(zip(decomp.codegen.cfunc.functy.args, decomp.codegen.cfunc.arg_list)):
            function['arguments'].append({"idx": idx, "name": arg_cvar.c_repr(), "type": arg_type.c_repr(), "size": arg_type.size // 8})
        for i in decomp.codegen.cfunc.variables_in_use:
            var = decomp.codegen.cfunc.variable_manager.unified_variable(i)
            var_info = {}
            if hasattr(var, 'name'):
                var_info['name'] = var.name
            if hasattr(var, 'offset'):
                var_info['offset'] = var.offset
            if var_info:
                function['variables'].append(var_info)
        binary_info["functions"][str(addr)] = function

    with open(output_path, 'w') as f:
        toml.dump(binary_info, f)

if __name__ == "__main__":
    binary_path = sys.argv[1]
    output_path = sys.argv[2]
    parse_binary(binary_path, output_path)
