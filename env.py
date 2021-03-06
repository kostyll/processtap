"""
  Copyright notice
  ================
  
  Copyright (C) 2010
      Lorenzo  Martignoni <martignlo@gmail.com>
      Roberto  Paleari    <roberto.paleari@gmail.com>
  
  This program is free software: you can redistribute it and/or modify it under
  the terms of the GNU General Public License as published by the Free Software
  Foundation, either version 3 of the License, or (at your option) any later
  version.
  
  ProcessTap is distributed in the hope that it will be useful, but WITHOUT ANY
  WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
  A PARTICULAR PURPOSE.  See the GNU General Public License for more details.
  
  You should have received a copy of the GNU General Public License along with
  this program. If not, see <http://www.gnu.org/licenses/>.
  
"""

# CPU state description and accessors 

import struct
import sys
import platform
import processtap
import event
import symbol
import prototype

LITTLE_ENDIAN = 0
BIG_ENDIAN    = 1

class initargs:
    def __init__(self):
        self.rr = None
        self.wr = None
        self.rm = None
        self.wm = None
        self.abibuilder = None
        self.envbuilder = None
__initargs = initargs()

class regs:
    def __init__(self, cpu, r, rr, wr):
        self.__dict__["cpu"] = cpu
        self.__dict__["regs"] = r
        self.__dict__["read_callback"] = rr
        self.__dict__["write_callback"] = wr

    def __getattr__(self, reg):
        if reg not in self.__dict__["regs"]:
            raise AttributeError("CPU invalid register '%s' for cpu '%s'" % (reg, self.__dict__["cpu"]))            
        return self.__dict__["read_callback"](self.__dict__["regs"][reg])

    def __setattr__(self, reg, value):
        if reg not in self.__dict__["regs"]:
            raise AttributeError("CPU invalid register '%s' for cpu '%s'" % (reg, self.__dict__["cpu"]))            
        return self.__dict__["write_callback"](self.__dict__["regs"][reg], value)


class mem:
    def __init__(self, env, rm, wm):
        self.read_callback = rm
        self.write_callback = wm

    def __getitem__(self, addr):
        if isinstance(addr, slice):
            # Slice access
            start = addr.start
            stop  = addr.stop
        elif isinstance(addr, tuple) and len(addr) == 2:
            start, stop = addr
        elif isinstance(addr, (int, long)):
            # Non-slice access
            start = addr
            stop  = None
        else:
            assert 0

        if start is None:
            start = 0
        elif isinstance(start, str):
            start = sym_lookup(start)[0]

        if stop is None:
            stop = start + 1
        elif isinstance(stop, str):
            stop = sym_lookup(stop)[0]

        size = stop - start

        return self.read_callback(start, size)
    
    def __setitem__(self, addr, value):
        if isinstance(value, int) or isinstance(value, long):
            value = struct.pack("I", value)
        if isinstance(addr, str):
            addr = symbol.get_symbol_strict(addr)[0]
        return self.write_callback(addr, len(value), value)

    def __setslice__(self, start, stop, value):
        if isinstance(value, int) or isinstance(value, long):
            value = struct.pack("I", value)
        if isinstance(start, str):
            start = symbol.get_symbol_strict(start)[0]
        if isinstance(stop, str):
            stop = symbol.get_symbol_strict(stop)[0]
        assert len(value) == stop - start
        return self.write_callback(start, stop - start, value)


class args:
    def __init__(self, env):
        self.env = env

    def __getitem__(self, item):
        if isinstance(self.env.event, (event.function_exit, event.function_entry)):
            f = self.env.function_name
        elif isinstance(self.env.event, (event.syscall_exit, event.syscall_entry)):
            f = self.env.syscall_name
        else:
            raise Exception()

        return prototype.peek_argument(self.env, f, item)


    def __len__(self):
        if isinstance(self.env.event, (event.function_exit, event.function_entry)):
            f = self.env.function_name
        elif isinstance(self.env.event, (event.syscall_exit, event.syscall_entry)):
            f = self.env.syscall_name
        else:
            raise Exception()

        p = prototype.get_prototype(f)
        if p:
            return len(p.getArguments())
        else:
            raise prototype.MissingPrototype("Missing prototype for '%s'" % (f))
            
    def __setitem__(self, item, value):
        if not isinstance(self.event, (event.FUNCTION_ENTRY, event.FUNCTION_EXIT, event.SYSCALL_ENTRY, event.SYSCALL_EXIT)):
            raise Exception()

    def __getattr__(self, item):
        if item == "retval":
            if isinstance(self.env.event, event.function_exit):
                f = self.env.function_name
            elif isinstance(self.env.event, event.syscall_exit):
                f = self.env.syscall_name
            else:
                raise Exception()

            return prototype.peek_argument(self.env, f, -1)

        raise Exception()


class env:
    def __init__(self, cpu, r, rr, wr, rm, wm, endianess, abi):
        self.cpu = cpu
        self.regs = regs(self, r, rr, wr)
        self.mem = mem(self, rm, wm)
        self.modules = []
        self.args = args(self)
        self.event = None
        self.endianess = endianess
        self.abi = abi(self)

        self.__attributes = {
            "exe" : (None, self.__exe),
            "module" : (None, self.__module),
            "pid" : (None, self.__pid),
            "tid" : (None, self.__tid),
            "function": (lambda ev: isinstance(ev, (event.function_entry, event.function_exit)), self.__function),
            "function_name": (lambda ev: isinstance(ev, (event.function_entry, event.function_exit)), self.__function_name),
            "caller": (lambda ev: isinstance(ev, (event.function_entry, event.function_exit)), self.__caller),
            "callee": (lambda ev: isinstance(ev, (event.function_entry, event.function_exit)), self.__callee),
            "syscall": (lambda ev: isinstance(ev, (event.syscall_entry, event.syscall_exit)), self.__syscall),
            "syscall_name": (lambda ev: isinstance(ev, (event.syscall_entry, event.syscall_exit)), self.__syscall_name),
            "prototype": (lambda ev: isinstance(ev, (event.function_entry, event.function_exit, event.syscall_entry, event.syscall_exit)), 
                          self.__prototype),
            "retval": (lambda ev: isinstance(ev, (event.function_exit, event.syscall_exit)), self.__retval),
            }

    def isLittleEndian(self):
        return self.endianess == LITTLE_ENDIAN

    def isBigEndian(self):
        return self.endianess == BIG_ENDIAN

    def __attribute_getter(self, name):
        if name in self.__attributes:
            cond, getter = self.__attributes[name]
            if cond is None or cond(self.event):
                return getter()
        raise AttributeError("Invalid attribue '%s' for event '%s'" % (name, event.event2str[self.event.type]))

    def __getattr__(self, name):
        return self.__attribute_getter(name)

    def __exe(self):
        return "randomprocname"
    
    def __module(self):
        m = symbol.get_module(self.event.instruction)
        if m:
            return m
        else:
            return "unknown"

    def __pid(self):
        return self.event.pid

    def __tid(self):
        return self.event.tid

    def __function(self):
        return self.event.function

    def __syscall(self):
        return self.event.sysno

    def __callee(self):
        return self.event.function

    def __function_name(self):
        s = symbol.get_symbol(self.event.function)
        if s: return s[0]
        else: return None

    def __prototype(self):
        fname = self.functionname()
        if fname:
            return prototype.get_prototype(fname)
        else:
            return None

    def __instruction(self):
        return self.event.instruction

    def __syscall_name(self):
        return symbol.get_syscall(self.event.sysno)

    def __caller(self):
        return self.event.instruction

    def __retval(self):
        return self.args.retval


class env_x86(env):
    RAX = EAX = 0
    RBX = EBX = 1
    RCX = ECX = 2
    RDX = EDX = 3
    RSI = ESI = 4
    RDI = EDI = 5
    RSP = ESP = 6
    RBP = EBP = 7
    RFLAGS = EFLAGS = 8
    RIP = EIP = 9
    STACKPTR = RSP
    INSTPTR = RIP

    PTR_SIZE = 4

    def __init__(self, rr, wr, rm, wm, abi):
        env.__init__(self, "i686", {
                "EAX" : env_x86.EAX,
                "EBX" : env_x86.EBX,
                "ECX" : env_x86.ECX,
                "EDX" : env_x86.EDX,
                "ESI" : env_x86.ESI,
                "EDI" : env_x86.EDI,
                "ESP" : env_x86.ESP,
                "EBP" : env_x86.EBP,
                "EFLAGS" : env_x86.EFLAGS,
                "EIP" : env_x86.EIP,
                "RAX" : env_x86.EAX,
                "RBX" : env_x86.EBX,
                "RCX" : env_x86.ECX,
                "RDX" : env_x86.EDX,
                "RSI" : env_x86.ESI,
                "RDI" : env_x86.EDI,
                "RSP" : env_x86.ESP,
                "RBP" : env_x86.EBP,
                "RFLAGS" : env_x86.EFLAGS,
                "RIP" : env_x86.EIP,
                "STACKPTR" : env_x86.ESP,
                "INSTPTR" : env_x86.EIP}, rr, wr, rm, wm, LITTLE_ENDIAN, abi)


class env_x86_64(env):
    RAX = 0
    RBX = 1
    RCX = 2
    RDX = 3
    RSI = 4
    RDI = 5
    RSP = 6
    RBP = 7
    RFLAGS = 8
    RIP = 9
    R8  = 10
    R9  = 11
    R10 = 12
    R11 = 13
    R12 = 14
    R13 = 15
    R14 = 16
    R15 = 17
    STACKPTR = RSP
    INSTPTR = RIP

    PTR_SIZE = 8

    def __init__(self, rr, wr, rm, wm, abi):
        env.__init__(self, "x86_64", {
                "RAX" : env_x86_64.RAX,
                "RBX" : env_x86_64.RBX,
                "RCX" : env_x86_64.RCX,
                "RDX" : env_x86_64.RDX,
                "RSI" : env_x86_64.RSI,
                "RDI" : env_x86_64.RDI,
                "RSP" : env_x86_64.RSP,
                "RBP" : env_x86_64.RBP,
                "RFLAGS" : env_x86_64.RFLAGS,
                "RIP" : env_x86_64.RIP,
                "R8" : env_x86_64.R8,
                "R9" : env_x86_64.R9,
                "R10" : env_x86_64.R10,
                "R11" : env_x86_64.R11,
                "R12" : env_x86_64.R12,
                "R13" : env_x86_64.R13,
                "R14" : env_x86_64.R14,
                "R15" : env_x86_64.R15,
                "STACKPTR" : env_x86_64.RSP,
                "INSTPTR" : env_x86_64.RIP}, rr, wr, rm, wm, LITTLE_ENDIAN, abi)
    
# Very simplified ABIs
class abi:
    def __init__(self, env):
        self.env = env

    def argument(i):
        raise Exception("Abastract")

    def returnval(i):
        raise Exception("Abastract")


class abi_x86_linux(abi):
    def __init__(self, env):
        abi.__init__(self, env)

    def argument(self, i):
        if isinstance(self.env.event, (event.function_entry, event.function_exit)):
            stackptr = self.env.regs.STACKPTR
            if isinstance(self.env.event, event.function_exit):
                stackptr += self.env.PTR_SIZE
            return self.env.mem[stackptr + i*self.env.PTR_SIZE, stackptr + (i+1)*self.env.PTR_SIZE]
        elif isinstance(self.env.event, (event.syscall_entry, event.syscall_exit)):
            __regs = ["EBX", "ECX", "EDX", "ESI", "EDI"]
            assert i < len(__regs)
            return struct.pack("=I", getattr(self.env.regs, __regs[i]))
        else:
            assert 0

    def returnval(self):
        return struct.pack("=Q", self.env.regs.REX)
    

class abi_x86_64_linux(abi):
    def __init__(self, env):
        abi.__init__(self, env)

    def argument(self, i):
        if isinstance(self.env.event, (event.function_entry, event.function_exit)):
            stackptr = self.env.regs.STACKPTR
            if isinstance(self.env.event, event.function_exit):
                stackptr += self.env.PTR_SIZE

            __regs = ["RDI", "RSI", "RDX", "RCX", "R8", "R9"]
            if i < len(__regs):
                return struct.pack("=Q", getattr(self.env.regs, __regs[i]))
            else:
                i = i - len(__regs)
                return self.env.mem[stackptr + i*ctx.PTR_SIZE, stackptr + (i+1)*ctx.PTR_SIZE]

        elif isinstance(self.env.event, (event.syscall_entry, event.syscall_exit)):
            __regs = ["RDI", "RSI", "RDX", "RCX", "R8", "R9"]
            assert i < len(__regs)
            return struct.pack("=Q", getattr(self.env.regs, __regs[i]))

        else:
            assert 0

    def returnval(self):
        return struct.pack("=Q", self.env.regs.RAX)

 
def init(rr, wr, rm, wm):
    global __initargs

    __initargs.rr = rr
    __initargs.wr = wr
    __initargs.rm = rm
    __initargs.wm = wm

    mac = platform.machine()
    sys = platform.system()
    if mac == 'i686':
        __initargs.envbuilder = env_x86
    elif mac == 'x86_64':
        __initargs.envbuilder = env_x86_64
    else:
        raise "[!] Unsupported architecture '%s'" % mac

    if mac == 'i686' and sys == 'Linux':
        __initargs.abibuilder = abi_x86_linux
    elif mac == 'x86_64' and sys == 'Linux':
        __initargs.abibuilder = abi_x86_64_linux
    else:
        raise "[!] Unsupported ABI '%s on %s'" % (sys, mac)


def build():
    global __initargs
    return __initargs.envbuilder(__initargs.rr, __initargs.wr, 
                                 __initargs.rm, __initargs.wm,
                                 __initargs.abibuilder)


