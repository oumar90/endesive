# *-* coding: utf-8 *-*
import hashlib
import re
from io import BytesIO

from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfparser import PDFParser
from pdfminer.pdftypes import PDFObjRef
from pdfminer.psparser import PSKeyword, PSLiteral
from pdfminer.utils import isnumber

from endesive import signer

ESC_PAT = re.compile(r'[\000-\037&<>()"\042\047\134\177-\377]')


def e(s):
    return ESC_PAT.sub(lambda m: '&#%d;' % ord(m.group(0)), s)


class SignedData(object):

    def aligned(self, data):
        data = data.hex().encode('utf-8')
        nb = 0x4000 - len(data)
        data = data + b'0' * (0x4000 - len(data))
        return data

    def dumpobj(self, out, obj):
        if obj is None:
            out.write(b'null ')
            return

        if isinstance(obj, dict):
            out.write(b' << ')
            for (k, v) in obj.items():
                out.write(b'/%s ' % bytes(k, 'utf-8'))
                self.dumpobj(out, v)
            out.write(b' >> ')
            return

        if isinstance(obj, list):
            out.write(b' [ ')
            for v in obj:
                self.dumpobj(out, v)
            out.write(b' ] ')
            return

        if isinstance(obj, bytes):
            out.write(b' ( ')
            out.write(obj)
            out.write(b' ) ')
            return

        if isinstance(obj, str):
            out.write(b' ( ')
            out.write(bytes(e(obj), 'utf-8'))
            out.write(b' ) ')
            return

        if isnumber(obj):
            if isinstance(obj, float):
                s = (b' %.5f ' % obj).rstrip(b'0')
            else:
                s = b' %d ' % obj
            out.write(s)
            return

        if isinstance(obj, PDFObjRef):
            out.write(b' %d 0 R ' % (obj.objid))
            return

        if isinstance(obj, PSKeyword):
            out.write(b' /%s ' % bytes(obj.name, 'utf-8'))
            return

        if isinstance(obj, PSLiteral):
            out.write(b' /%s ' % bytes(obj.name, 'utf-8'))
            return

        # if isinstance(obj, PDFStream):
        raise TypeError(obj)

    def getdata(self, pdfdata1, objid, startxref, document):
        obj = document.getobj(objid)
        fp = BytesIO()
        self.dumpobj(fp, obj)
        data = fp.getvalue().strip()
        return data[2:-2]

    def makeobj(self, no, data, datas=b''):
        return (b'%d 0 obj\n<<' % no) + data + b'>>\n'+datas+b'endobj\n'

    def makepdf(self, pdfdata1, udct, zeros):
        parser = PDFParser(BytesIO(pdfdata1))
        document = PDFDocument(parser, fallback=False)

        prev = document.find_xref(parser)
        info = document.xrefs[0].trailer['Info'].objid
        root = document.xrefs[0].trailer['Root'].objid
        size = document.xrefs[0].trailer['Size']
        page = document.getobj(document.catalog['Pages'].objid)['Kids'][0].objid

        infodata = self.getdata(pdfdata1, info, prev, document).strip()
        rootdata = self.getdata(pdfdata1, root, prev, document).strip()
        pagedata = self.getdata(pdfdata1, page, prev, document).strip()

        no = size
        objs = [
            self.makeobj(page, (b'/Annots[%d 0 R]' % (no + 3)) + pagedata),
            self.makeobj(no + 0, infodata),
            self.makeobj(no + 1, (b'/AcroForm %d 0 R' % (no + 2)) + rootdata),
            self.makeobj(no + 2, b'/Fields[%d 0 R]/SigFlags %d' % (no + 3, udct[b'sigflags'])),
            self.makeobj(no + 3,
                         b'/AP<</N %d 0 R>>/F 132/FT/Sig/P %d 0 R/Rect[0 0 0 0]/Subtype/Widget/T(Signature1)/V %d 0 R' % (
                             no + 4, page, no + 5)),
            self.makeobj(no + 4, b'/BBox[0 0 0 0]/Filter/FlateDecode/Length 8/Subtype/Form/Type/XObject',
                        b'stream\n\x78\x9C\x03\x00\x00\x00\x00\x01\nendstream\n'),
            self.makeobj(no + 5, (b'/ByteRange [0000000000 0000000000 0000000000 0000000000]/ContactInfo(%s)\
/Filter/Adobe.PPKLite/Location(%s)/M(D:%s)/Prop_Build<</App<</Name/>>>>/Reason(%s)/SubFilter/adbe.pkcs7.detached/Type/Sig\
/Contents <' % (udct[b'contact'], udct[b'location'], udct[b'signingdate'], udct[b'reason'])) + zeros + b'>'),
        ]

        pdfdata2 = b''.join(objs)
        xref = b'''\
xref\n\
%(page)d 1\n\
%(p0)010d 00000 n \n\
%(no)d 6\n\
%(n0)010d 00000 n \n\
%(n1)010d 00000 n \n\
%(n2)010d 00000 n \n\
%(n3)010d 00000 n \n\
%(n4)010d 00000 n \n\
%(n5)010d 00000 n \n\
'''
        startxref = len(pdfdata1)
        dct = {
            b'page': page,
            b'no': no,
            b'startxref': startxref + len(pdfdata2),
            b'prev': prev,
            b'info': no + 0,
            b'root': no + 1,
            b'size': 6,
            b'p0': startxref + pdfdata2.find(b'\n%d 0 obj\n' % page) + 1,
            b'n0': startxref + pdfdata2.find(b'\n%d 0 obj\n' % (no + 0)) + 1,
            b'n1': startxref + pdfdata2.find(b'\n%d 0 obj\n' % (no + 1)) + 1,
            b'n2': startxref + pdfdata2.find(b'\n%d 0 obj\n' % (no + 2)) + 1,
            b'n3': startxref + pdfdata2.find(b'\n%d 0 obj\n' % (no + 3)) + 1,
            b'n4': startxref + pdfdata2.find(b'\n%d 0 obj\n' % (no + 4)) + 1,
            b'n5': startxref + pdfdata2.find(b'\n%d 0 obj\n' % (no + 5)) + 1,
            b'h1': hashlib.md5(pdfdata1).hexdigest().upper().encode('ascii'),
            b'h2': hashlib.md5(pdfdata2).hexdigest().upper().encode('ascii'),
        }

        trailer = b'''\
trailer
<</ID [<%(h1)s><%(h2)s>]/Info %(info)d 0 R/Prev %(prev)d/Root %(root)d 0 R/Size %(size)d>>\n\
startxref\n\
%(startxref)d\n\
%%%%EOF\n\
'''

        xref = xref % dct
        trailer = trailer % dct

        pdfdata2 = pdfdata2 + xref + trailer

        return pdfdata2

    def sign(self, datau, dct, key, cert, othercerts, algomd):
        zeros = self.aligned(b'\0')

        pdfdata2 = self.makepdf(datau, dct, zeros)

        startxref = len(datau)
        pdfbr1 = pdfdata2.find(zeros)
        pdfbr2 = pdfbr1 + len(zeros)
        br = [0, startxref + pdfbr1 - 1, startxref + pdfbr2 + 1, len(pdfdata2) - pdfbr2 - 1]
        brfrom = b'[0000000000 0000000000 0000000000 0000000000]'
        brto = b'[%010d %010d %010d %010d]' % tuple(br)
        pdfdata2 = pdfdata2.replace(brfrom, brto, 1)

        md = getattr(hashlib, algomd)()
        md.update(datau)
        b1 = pdfdata2[:br[1] - startxref]
        b2 = pdfdata2[br[2] - startxref:]
        md.update(b1)
        md.update(b2)
        md = md.digest()

        contents = signer.sign(None, key, cert, othercerts, algomd, True, md)
        contents = self.aligned(contents)
        pdfdata2 = pdfdata2.replace(zeros, contents, 1)

        return pdfdata2


def sign(datau, udct, key, cert, othercerts, algomd):
    cls = SignedData()
    return cls.sign(datau, udct, key, cert, othercerts, algomd)
