#
# Copyright (C) 2011 EADS France, Fabrice Desclaux <fabrice.desclaux@eads.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
from miasm.expression.expression import *
import struct
import logging 
import cPickle
import numpy
from miasm.expression.expression_helper import *



numpy.seterr(over='ignore', under='ignore')

mymaxuint = {8:0xFFL,
             16:0xFFFFL,
             32:0xFFFFFFFFL,
             64:0xFFFFFFFFFFFFFFFFL    
             }


#expression evaluation in integer domain

tab_int_size = {int8:8,
                uint8:8,
                int16:16,
                uint16:16,
                int32:32,
                uint32:32,
                int64:64,
                uint64:64
                }

tab_intsize = {8:int8,
               16:int16,
               32:int32,
               64:int64
               }
tab_uintsize ={8:uint8,
               16:uint16,
               32:uint32,
               64:uint64
               }

tab_u2i = {uint8:int8,
           uint16:int16,
           uint32:int32}


class eval_abs:
    dict_size = {
        1:'B',
        2:'H',
        4:'I',
        }

    def parity(self, a):
        tmp = (a)&0xFFL
        cpt = 1
        while tmp!=0:
            cpt^=tmp&1
            tmp>>=1
        return cpt
        
    def my_bsf(self, a, default_val=0):
        tmp = 0
        for i in xrange(32):
            if a & (1<<i):
                return i
        
        return default_val
    def my_bsr(self, a, op_size, default_val = 0):
        tmp = 0
        for i in xrange(op_size-1, -1, -1):
            if a & (1<<i):
                return i
        
        return default_val
            
        
    def __init__(self, vars, func_read = None, func_write = None, log = None):
        self.pool = {}
        for v in vars:
            self.pool[v] = vars[v]
        self.func_read = func_read
        self.func_write = func_write
        if log == None:
            log = logging.getLogger("expr_eval_int")
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(logging.Formatter("%(levelname)-5s: %(message)s"))
            log.addHandler(console_handler)
            log.setLevel(logging.WARN)
        self.log = log

    def to_file(self, f):
        if type(f) is str:
            f = open(f,"w")
        self.log = None
        cPickle.dump(self, f)
    
    @staticmethod

    def from_file(f, g):
        if type(f) is str:
            f = open(f,"r")
        m = cPickle.load(f)
        log = logging.getLogger("expr_eval_int")
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter("%(levelname)-5s: %(message)s"))
        log.addHandler(console_handler)
        log.setLevel(logging.WARN)
        m.log = log
        new_pool = {}
        for x in m.pool:
            
            if not str(x) in g:
                xx = ExprId(str(x))
                g[str(xx)] = xx
            else:
                xx = x
            
            xx = x
            print repr(g[str(xx)]), g[str(xx)]
            
            if isinstance(m.pool[x], Expr):
                new_pool[g[str(xx)]] = m.pool[x].reload_expr(g)
            else:
                new_pool[g[str(xx)]] = m.pool[x]
                
        m.pool = new_pool
        return m

    def find_mem_by_addr(self, e):
        for k in self.pool:
            if not isinstance(k, ExprMem):
                continue
            if k.arg != e:
                continue
            return k
        return None


    def is_mem_in_target(self, e, t):
        ex = ExprOp('-', e.arg, t.arg)
        ex = expr_simp(self.eval_expr(ex, {}))
        if not isinstance(ex, ExprInt):
            return None
        ptr_diff = int32(ex.arg)
        if ptr_diff <0:
            return False
        if ptr_diff + e.size/8 <= t.size/8:
            return True
        return False

    def substract_mems(self, a, b):
        ex = ExprOp('-', b.arg, a.arg)
        ex = expr_simp(self.eval_expr(ex, {}))
        if not isinstance(ex, ExprInt):
            return None
        ptr_diff = int32(ex.arg)
        out = []
        if ptr_diff <0:
            #    [a     ]
            #[b      ]XXX
            
            sub_size = b.size + ptr_diff*8
            if sub_size >= a.size:
                pass
            else:
                ex = ExprOp('+', a.arg, ExprInt(uint32(sub_size)))
                ex = expr_simp(self.eval_expr(ex, {}))
                
                rest_ptr = ex
                rest_size = a.size - sub_size
    
                val = self.pool[a][sub_size:a.size]
                out = [(ExprMem(rest_ptr, rest_size), val)]
        else:
            #[a         ]
            #XXXX[b   ]YY

            #[a     ]
            #XXXX[b     ]

            out = []
            #part X
            if ptr_diff >0:
                val = self.pool[a][0:ptr_diff*8]
                out.append((ExprMem(a.arg, ptr_diff*8), val))
            #part Y
            if ptr_diff*8+b.size <a.size:
                
                ex = ExprOp('+', b.arg, ExprInt(uint32(b.size/8)))
                ex = expr_simp(self.eval_expr(ex, {}))

                rest_ptr = ex
                rest_size = a.size - (ptr_diff*8 + b.size)
                val = self.pool[a][ptr_diff*8 + b.size:a.size]
                out.append((ExprMem(ex, val.get_size()), val))
            
            
        return out    

    #give mem stored overlapping requested mem ptr
    def get_mem_overlapping(self, e):
        if not isinstance(e, ExprMem):
            raise ValueError('mem overlap bad arg')
        ov = []
        
        for k in self.pool:
            if not isinstance(k, ExprMem):
                continue
            ex = ExprOp('-', k.arg, e.arg)
            ex = expr_simp(self.eval_expr(ex, {}))
            if not isinstance(ex, ExprInt):
                continue
            ptr_diff = int32(ex.arg)
            if ptr_diff >=0 and ptr_diff < e.size/8:
                ov.append((-ptr_diff, k))
            elif ptr_diff <0 and ptr_diff + k.size/8>0:
                ov.append((-ptr_diff, k))

        return ov
                
    def eval_expr(self, e, eval_cache):
        if e.is_term:
            return e
        if e.is_eval:
            return e
        ret = self.eval_expr_no_cache(e, eval_cache)
        ret.is_eval = True
        return ret
        


    def eval_op_plus(self, args, op_size, cast_int):
        ret_value = args[0] + args[1]
        return ret_value

    def eval_op_minus(self, args, op_size, cast_int):
        ret_value = args[0] - args[1]
        return ret_value
    
    def eval_op_mult(self, args, op_size, cast_int):
        ret_value = (args[0] * args[1])
        return ret_value

    def eval_op_div(self, args, op_size, cast_int):
        a = uint64(args[0])
        b = uint64(args[1])
        c = uint64(args[2])
        if c == 0:
            raise ValueError('div by 0')
        big = (a<<uint64(op_size))+b
        ret_value =  big/c
        if ret_value>mymaxuint[op_size]:raise ValueError('Divide Error')
        return ret_value

    def eval_op_rem(self, args, op_size, cast_int):
        a = uint64(args[0])
        b = uint64(args[1])
        c = uint64(args[2])
        if c == 0:
            raise ValueError('div by 0')
        big = (a<<uint64(op_size))+b
        ret_value =  big-c*(big/c) 
        if ret_value>mymaxuint[op_size]:raise ValueError('Divide Error')        
        return ret_value

    def eval_op_idiv(self, args, op_size, cast_int):
        a = uint64(args[0])
        b = uint64(args[1])
        c = int64(tab_u2i[cast_int](args[2]))
        if c == 0:
            raise ValueError('div by 0')
        big = (a<<uint64(op_size))+b
        big = tab_intsize[op_size*2](big)
        ret_value =  big/c
        try:
            ret_value = tab_u2i[cast_int](ret_value)
        except:
            raise ValueError('Divide Error')
        return ret_value

    def eval_op_irem(self, args, op_size, cast_int):
        a = uint64(args[0])
        b = uint64(args[1])
        c = int64(tab_u2i[cast_int](args[2]))
        if c == 0:
            raise ValueError('div by 0')
        big = (a<<uint64(op_size))+b
        big = tab_intsize[op_size*2](big)
        ret_value =  big/c
        try:
            ret_value = tab_u2i[cast_int](ret_value)
        except:
            raise ValueError('Divide Error')
        ret_value = big-ret_value*c
        return ret_value

    def eval_op_mulhi(self, args, op_size, cast_int):
        a = uint64(args[0])
        b = uint64(args[1])
        ret_value =  (a*b) >> uint64(op_size)
        return ret_value

    def eval_op_mullo(self, args, op_size, cast_int):
        a = uint64(args[0])
        b = uint64(args[1])
        ret_value =  (a*b) & mymaxuint[op_size]
        return ret_value

    def eval_op_eq(self, args, op_size, cast_int):
        ret_value =  [0, 1][int(args[0] == args[1])]
        return ret_value

    def eval_op_inf(self, args, op_size, cast_int):
        ret_value =  [0, 1][int(args[0] < args[1])]
        return ret_value

    def eval_op_and(self, args, op_size, cast_int):
        ret_value = (args[0] & args[1])
        return ret_value

    def eval_op_or(self, args, op_size, cast_int):
        ret_value = (args[0] | args[1])
        return ret_value

    def eval_op_xor(self, args, op_size, cast_int):
        ret_value = (args[0] ^ args[1])
        return ret_value

    def eval_op_not(self, args, op_size, cast_int):
        ret_value = (args[0] ^ tab_uintsize[op_size](mymaxuint[op_size]))
        return ret_value

    def eval_op_rotl(self, args, op_size, cast_int):
        r = args[1]&0x1F
        r %=op_size
        ret_value = ((args[0]<<r) & mymaxuint[op_size]) | ((args[0] & mymaxuint[op_size]) >> (op_size-r))
        return ret_value

    def eval_op_rotr(self, args, op_size, cast_int):
        r = args[1]&0x1F
        r %=op_size
        ret_value = ((args[0] & mymaxuint[op_size])>>r)  | ((args[0] << (op_size-r)) & mymaxuint[op_size])
        return ret_value

    def eval_op_rotl_wflag(self, args, op_size, cast_int):
        r = args[1]&0x1F
        r %=op_size+1
        r = uint64(r)
        op_size = uint64(op_size)
        tmpa = uint64((args[0]<<1) | args[2])
        rez = (tmpa<<r) | (tmpa >> (op_size+uint64(1)-r))
        return rez

    def eval_op_rotl_wflag_rez(self, args, op_size, cast_int):
        return self.eval_op_rotl_wflag(args, op_size, cast_int)>>1
    def eval_op_rotl_wflag_cf(self, args, op_size, cast_int):
        return self.eval_op_rotl_wflag(args, op_size, cast_int)&1

    def eval_op_rotr_wflag(self, args, op_size, cast_int):
        r = args[1]&0x1F
        r %=op_size+1
        r = uint64(r)
        op_size = uint64(op_size)
        tmpa = uint64((args[0]<<1) | args[2])
        rez = (tmpa>>r)  | (tmpa << (op_size+uint64(1)-r))
        return rez
    
    def eval_op_rotr_wflag_rez(self, args, op_size, cast_int):
        return self.eval_op_rotr_wflag(args, op_size, cast_int)>>1
    def eval_op_rotr_wflag_cf(self, args, op_size, cast_int):
        return self.eval_op_rotr_wflag(args, op_size, cast_int)&1

    def eval_op_lshift(self, args, op_size, cast_int):
        r = args[1]#&0x1F
        ret_value = ((args[0] &mymaxuint[op_size])<<r)
        return ret_value
    
    def eval_op_rshift(self, args, op_size, cast_int):
        r = args[1]#&0x1F
        ret_value = ((args[0]&mymaxuint[op_size])>>r)
        return ret_value

    def eval_op_arshift(self, args, op_size, cast_int):
        r = args[1]#&0x1F
        if args[0]>=0:
            ret_value = ((args[0]&mymaxuint[op_size])>>r)
        else:
            ret_value = -((-args[0])>>r)
        return ret_value


    def eval_op_bsf(self, args, op_size, cast_int):
        ret_value = self.my_bsf(args[1], args[0])
        return ret_value

    def eval_op_bsr(self, args, op_size, cast_int):
        ret_value = self.my_bsr(args[1], op_size, args[0])
        return ret_value

    def eval_op_parity(self, args, op_size, cast_int):
        ret_value = self.parity(args[0])
        return ret_value

    def eval_op_int_32_to_double(self, args, op_size, cast_int):
        print args[0]
        return ExprTop()
        b = struct.pack('L', args[0])
        print repr(b)
        b = struct.unpack('f', b)[0]
        print b
        raise ValueError('not impl yet')
        ret_value = args[0]
        return ret_value

    def objbyid_default0(self, args, op_size, cast_int):
        return ExprOp("objbyid_default0", ExprInt(cast_int(args[0])))


    
    deal_op = {'+':eval_op_plus,
               '-':eval_op_minus,
               '*':eval_op_mult,
               '/div':eval_op_div,
               '/rem':eval_op_rem,
               '/idiv':eval_op_idiv,
               '/irem':eval_op_irem,
               '*hi':eval_op_mulhi,
               '*lo':eval_op_mullo,
               '==':eval_op_eq,
               '<':eval_op_inf,
               '&':eval_op_and,
               '|':eval_op_or,
               '^':eval_op_xor,
               '!':eval_op_not,
               '<<<':eval_op_rotl,
               '>>>':eval_op_rotr,
               '<<<c_rez':eval_op_rotl_wflag_rez,
               '<<<c_cf':eval_op_rotl_wflag_cf,
               '<<':eval_op_lshift,
               '>>':eval_op_rshift,
               'a>>':eval_op_arshift,
               'bsf':eval_op_bsf,
               'bsr':eval_op_bsr,
               'parity':eval_op_parity,
               'int_32_to_double':eval_op_int_32_to_double,

               #XXX
               'objbyid_default0':objbyid_default0,
               }
    
    op_size_no_check = ['<<<', '>>>', 'a<<', '>>', '<<',
                        '<<<c_rez', '<<<c_cf', 
                        '>>>c_rez', '>>>c_cf',]


    def eval_ExprId(self, e, eval_cache = {}):
        if not e in self.pool:
            return ExprTop(e)
        return self.pool[e]

    def eval_ExprInt(self, e, eval_cache = {}):
        return e
    
    def eval_ExprMem(self, e, eval_cache = {}):
        a_val = expr_simp(self.eval_expr(e.arg, eval_cache))
        if isinstance(a_val, ExprTop):
            #XXX hack test
            ee =   ExprMem(e.arg, e.size)
            ee.is_term = True
            return ee
        
        
        a = expr_simp(ExprMem(a_val, size = e.size))
        if a in self.pool:
            return self.pool[a]
        
        tmp = None
        #test if mem lookup is known
        for k in self.pool:
            if not isinstance(k, ExprMem):
                continue
            if a_val == k.arg:
                tmp = k
                break
        if tmp == None:

            v = self.find_mem_by_addr(a_val)
            if not v:
                out = []
                ov = self.get_mem_overlapping(a)
                off_base = 0
                ov.sort()
                ov.reverse()
                for off, x in ov:
                    if off >=0:
                        m = min(a.get_size(), x.get_size()-off*8)
                        ee = ExprSlice(self.pool[x], off*8, off*8 + m)
                        ee = expr_simp(ee)
                        out.append(ExprSliceTo(ee, off_base, off_base+ee.get_size()))
                        off_base += ee.get_size()
                    else:
                        m = min(a.get_size()+off*8, x.get_size())
                        ee = ExprSlice(self.pool[x], 0, m)
                        ee = expr_simp(ee)
                        out.append(ExprSliceTo(ee, off_base, off_base+ee.get_size()))
                        off_base += ee.get_size()
                if out:
                    ee = ExprSlice(ExprCompose(out), 0, a.get_size())
                    ee = expr_simp(ee)
                    return ee
            if self.func_read and isinstance(a.arg, ExprInt):
                return self.func_read(self, a)
            else:
                #XXX hack test
                a.is_term = True
                return a
        
        #eq lookup
        if a.size == k.size:
            return self.pool[tmp]
        
        #bigger lookup
        if a.size > k.size:
            rest = a.size
            ptr = a_val
            out = []
            ptr_index = 0
            while rest:
                v = self.find_mem_by_addr(ptr)
                if v == None:
                    raise ValueError("cannot find %s in mem"%str(ptr))

                if (rest-v.size) >=0:
                    val = self.pool[v]
                    diff_size = v.size
                else:
                    diff_size = v.size-rest
                    val = self.pool[v][0:diff_size]
                    
                val = ExprSliceTo(val, ptr_index, ptr_index+diff_size)
                
                out.append(val)
                ptr_index+=diff_size
                rest -= diff_size
                ptr = expr_simp(self.eval_expr(ExprOp('+', ptr, ExprInt(uint32(v.size/8))), eval_cache))
            e = expr_simp(ExprCompose(out))
            return e
        
        #part lookup
        tmp = expr_simp(ExprSlice(self.pool[tmp], 0, a.size))
        
        return tmp
    
    def eval_ExprOp(self, e, eval_cache = {}):
        args = []
        for a in e.args:
            b = expr_simp(self.eval_expr(a, eval_cache))
            if isinstance(b, ExprTop):
                return ExprTop()
            args.append(b)
        #Very agresive, but should work
        for a in args:
            if isinstance(a, ExprTop):
                return ExprTop()
        
        for a in args:
            if not isinstance(a, ExprInt):
                return ExprOp(e.op, *args)
        
        args = [a.arg for a in args]
        
        types_tab = [type(a) for a  in args]
        if types_tab.count(types_tab[0]) != len(args) and not e.op in self.op_size_no_check:
            raise 'invalid cast %s %s'%(str(types_tab), str(args))
        
        cast_int = types_tab[0]
        op_size = tab_int_size[types_tab[0]]
        

        ret_value = self.deal_op[e.op](self, args, op_size, cast_int)
        if isinstance(ret_value, Expr):
            return ret_value
        return ExprInt(cast_int(ret_value))
                   
    def eval_ExprCond(self, e, eval_cache = {}):
        cond = self.eval_expr(e.cond, eval_cache)
        src1 = self.eval_expr(e.src1, eval_cache)
        src2 = self.eval_expr(e.src2, eval_cache)

        if isinstance(cond, ExprTop):
            return ExprCond(e.cond, src1, src2)
        
        if isinstance(cond, ExprInt):
            if cond.arg == 0:
                return src2
            else:
                return src1
        return ExprCond(cond, src1, src2)
       
    def eval_ExprSlice(self, e, eval_cache = {}):
        arg = expr_simp(self.eval_expr(e.arg, eval_cache))
        if isinstance(arg, ExprTop):
            return ExprTop()
        
        if isinstance(arg, ExprMem):
            if e.start == 0 and e.stop == arg.size:
                return arg
                
            return ExprSlice(arg, e.start, e.stop)
        if isinstance(arg, ExprTop):
            return ExprTop()
        if isinstance(arg, ExprId):
            return ExprSlice(arg, e.start, e.stop)
        if isinstance(arg, ExprInt):
            return expr_simp(ExprSlice(arg, e.start, e.stop))
        if isinstance(arg, ExprCompose):
            to_add = []
            return ExprSlice(arg, e.start, e.stop)
        return ExprSlice(arg, e.start, e.stop)
            
    def eval_ExprCompose(self, e, eval_cache = {}):
        args = []
        for a in e.args:
            aa = self.eval_expr(a.arg, eval_cache)
            if isinstance(aa, ExprTop):
                return ExprTop()
            else:
                args.append(aa)
        for a in args:
            if isinstance(a, ExprTop):
                return ExprTop()
        is_int = True
        is_int_cond = 0
        for x in args:
            if isinstance(x, ExprInt):
                continue
            is_int = False
            if not isinstance(x, ExprCond) or not (isinstance(x.src1, ExprInt) and isinstance(x.src2, ExprInt)):
                is_int_cond+=3
                continue
            is_int_cond+=1
                
        
        if not is_int and is_int_cond!=1:
            uu = ExprCompose([ExprSliceTo(a, e.args[i].start, e.args[i].stop) for i, a in enumerate(args)])
            return uu

        if not is_int:
            rez = 0L
            total_bit = 0
            
            for i in xrange(len(e.args)):
                if isinstance(args[i], ExprInt):
                    a = args[i].arg

                    mask = (1<<(e.args[i].stop-e.args[i].start))-1
                    a&=mask
                    a<<=e.args[i].start
                    total_bit+=e.args[i].stop-e.args[i].start
                    rez|=a
                else:
                    a = args[i]
                    mask = (1<<(e.args[i].stop-e.args[i].start))-1
                    total_bit+=e.args[i].stop-e.args[i].start
                    mycond, mysrc1, mysrc2 = a.cond, a.src1.arg&mask, a.src2.arg&mask
                    cond_i = i
                    
            mysrc1|=rez
            mysrc2|=rez
            
            
            
            if total_bit in tab_uintsize:
                return self.eval_expr(ExprCond(mycond, ExprInt(tab_uintsize[total_bit](mysrc1)), ExprInt(tab_uintsize[total_bit](mysrc2))), eval_cache)
            else:
                raise 'cannot return non rounb bytes rez! %X %X'%(total_bit, rez)
                    
                
        
        rez = 0L
        total_bit = 0
        for i in xrange(len(e.args)):
            a = args[i].arg
            mask = (1<<(e.args[i].stop-e.args[i].start))-1
            a&=mask
            a<<=e.args[i].start
            total_bit+=e.args[i].stop-e.args[i].start
            rez|=a
        if total_bit in tab_uintsize:
            return ExprInt(tab_uintsize[total_bit](rez))
        else:
            raise 'cannot return non rounb bytes rez! %X %X'%(total_bit, rez)
        
    def eval_ExprTop(self, e, eval_cache = {}):
        return e
    
    def eval_expr_no_cache(self, e, eval_cache = {}):
        c = e.__class__
        deal_class = {ExprId: self.eval_ExprId,
                      ExprInt: self.eval_ExprInt,
                      ExprMem: self.eval_ExprMem,
                      ExprOp: self.eval_ExprOp,
                      ExprCond:self.eval_ExprCond,
                      ExprSlice: self.eval_ExprSlice,
                      ExprCompose:self.eval_ExprCompose,
                      ExprTop:self.eval_ExprTop,
                      }
        return deal_class[c](e, eval_cache)

    def get_instr_mod(self, exprs):
        pool_out = {}
        
        eval_cache = {}
        
        for e in exprs:
            if not isinstance(e, ExprAff):
                raise TypeError('not affect', str(e))
            
            src = self.eval_expr(e.src, eval_cache)
            if isinstance(e.dst, ExprMem):
                a = self.eval_expr(e.dst.arg, eval_cache)
                a = expr_simp(a)
                #search already present mem
                tmp = None
                #test if mem lookup is known
                tmp = ExprMem(a, e.dst.size)
                dst = tmp
                if self.func_write and isinstance(dst.arg, ExprInt):
                    self.func_write(self, dst, src, pool_out)
                else:
                    pool_out[dst] = src
                
            elif isinstance(e.dst, ExprId):
                pool_out[e.dst] = src
            elif isinstance(e.dst, ExprTop):
                raise ValueError("affect in ExprTop")
            else:
                raise ValueError("affected zarb", str(e.dst))
                

        return pool_out    

    def eval_instr(self, exprs):
        tmp_ops = self.get_instr_mod(exprs)
        cste_propag = True
        mem_dst = []
        for op in tmp_ops:
            if isinstance(op, ExprMem):
                ov = self.get_mem_overlapping(op)
                for off, x in ov:
                    diff_mem = self.substract_mems(x, op)
                    del(self.pool[x])
                    for xx, yy in diff_mem:
                        self.pool[xx] = yy
                tmp = expr_simp(tmp_ops[op])
                    
                if isinstance(expr_simp(op.arg), ExprTop):
                    raise ValueError('xx')
                    continue
            else:
                tmp = tmp_ops[op]
                tmp = expr_simp(tmp)

            if isinstance(tmp, ExprInt) and isinstance(op, ExprId) and op.name in ['zf','nf', 'pf', 'of', 'cf', 'df']:
                tmp = ExprInt(uint32(tmp.arg))
            self.pool[op] = tmp
            if isinstance(op, ExprMem):
                mem_dst.append(op)

            
        return mem_dst

    def get_reg(self, r):
        return self.eval_expr(self.pool[r], {})
        



