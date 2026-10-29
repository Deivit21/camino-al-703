"""Repara archivos inProgressActivity.fit de Zwift para poder subirlos a Garmin Connect.
Quita vueltas vacías (timestamp 0), corrige num_laps y la hora local de la actividad,
y recalcula tamaños y CRC. Uso: python reparar_fit.py entrada.fit salida.fit"""
import struct, sys, collections, fitparse

def crc16(b):
    t=[0x0000,0xCC01,0xD801,0x1400,0xF001,0x3C00,0x2800,0xE401,0xA001,0x6C00,0x7800,0xB401,0x5000,0x9C01,0x8801,0x4400]
    c=0
    for x in b:
        tmp=t[c&0xF]; c=(c>>4)&0x0FFF; c=c^tmp^t[x&0xF]
        tmp=t[c&0xF]; c=(c>>4)&0x0FFF; c=c^tmp^t[(x>>4)&0xF]
    return c

def reparar(src, dst, tz_offset=7200):
    raw=bytearray(open(src,'rb').read())
    hs=raw[0]; ds=int.from_bytes(raw[4:8],'little')
    data=raw[hs:min(len(raw),hs+ds)]
    defs={}; out=bytearray(); i=0; dropped=0; laps=0; fixes=[]
    session_pos=None
    while i<len(data):
        start=i; h=data[i]; i+=1
        if h&0x80:
            d=defs[(h>>5)&3]; i+=d['size']; out+=data[start:i]; continue
        lt=h&0x0F
        if h&0x40:
            e='<' if data[i+1]==0 else '>'
            g=struct.unpack(e+'H',data[i+2:i+4])[0]; nf=data[i+4]; i+=5
            fields=[]
            for _ in range(nf): fields.append((data[i],data[i+1])); i+=3
            size=sum(f[1] for f in fields)
            if h&0x20:
                nd=data[i]; i+=1
                for _ in range(nd): size+=data[i+1]; i+=3
            defs[lt]={'g':g,'fields':fields,'size':size,'e':e}
            out+=data[start:i]; continue
        d=defs[lt]; body=bytearray(data[i:i+d['size']]); i+=d['size']
        if len(body)<d['size']: fixes.append('mensaje final truncado eliminado'); break
        off={}; o=0
        for fn,fs in d['fields']: off[fn]=(o,fs); o+=fs
        e=d['e']
        if d['g']==19 and 253 in off:
            ts=struct.unpack(e+'I',body[off[253][0]:off[253][0]+4])[0]
            if ts==0: dropped+=1; continue
            laps+=1
        if d['g']==18 and 26 in off:
            session_pos=(len(out)+1+off[26][0], e)
        if d['g']==34 and 5 in off and 253 in off:
            ts=struct.unpack(e+'I',body[off[253][0]:off[253][0]+4])[0]
            lo=struct.unpack(e+'I',body[off[5][0]:off[5][0]+4])[0]
            if lo<ts-86400 or lo>ts+86400:
                body[off[5][0]:off[5][0]+4]=struct.pack(e+'I',ts+tz_offset); fixes.append('hora local corregida')
        out+=bytes([h])+body
    if session_pos:
        p,e=session_pos; n=struct.unpack(e+'H',out[p:p+2])[0]
        if n!=laps: out[p:p+2]=struct.pack(e+'H',laps); fixes.append(f'num_laps {n}->{laps}')
    if dropped: fixes.append(f'{dropped} vuelta(s) vacía(s) eliminada(s)')
    hdr=bytearray(raw[:hs]); hdr[4:8]=len(out).to_bytes(4,'little')
    if hs>=14: hdr[12:14]=crc16(hdr[:12]).to_bytes(2,'little')
    fb=bytes(hdr)+bytes(out); fb+=crc16(fb).to_bytes(2,'little')
    open(dst,'wb').write(fb)
    ff=fitparse.FitFile(dst)
    c=collections.Counter(m.name for m in ff.get_messages())
    return fixes, c

if __name__=='__main__':
    fixes,c=reparar(sys.argv[1], sys.argv[2]); print('Arreglos:',fixes or 'ninguno'); print(dict(c))
