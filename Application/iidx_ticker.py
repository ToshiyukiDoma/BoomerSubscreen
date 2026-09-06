"""Nine 16-segment cells driven by the read-only IIDX ticker API."""
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QColor, QPainter, QPolygonF, QPixmap
from PySide6.QtWidgets import QWidget
import math

# Split horizontal bars, four side bars, four diagonals, two center verticals.
SEGMENTS = [((14,8),(48,8)), ((52,8),(86,8)),
            ((90,14),(90,91)), ((90,109),(90,186)),
            ((52,192),(86,192)), ((14,192),(48,192)),
            ((10,109),(10,186)), ((10,14),(10,91)),
            ((14,100),(46,100)), ((54,100),(86,100)),
            ((20,20),(43,86)), ((50,18),(50,86)),
            ((80,20),(57,86)), ((57,114),(80,180)),
            ((50,114),(50,182)), ((43,114),(20,180))]
GLYPHS = {
    ' ':(), '0':(0,1,2,3,4,5,6,7), '1':(2,3),
    '2':(0,1,2,8,9,6,4,5), '3':(0,1,2,3,4,5,8,9),
    '4':(7,8,9,2,3), '5':(0,1,7,8,9,3,4,5),
    '6':(0,1,7,8,9,6,3,4,5), '7':(0,1,2,3),
    '8':(0,1,2,3,4,5,6,7,8,9), '9':(0,1,2,3,4,5,7,8,9),
    'A':(0,1,2,3,6,7,8,9), 'B':(0,1,2,3,4,5,8,9,11,14),
    'C':(0,1,4,5,6,7), 'D':(0,1,2,3,4,5,11,14),
    'E':(0,1,4,5,6,7,8,9), 'F':(0,1,6,7,8,9),
    'G':(0,1,3,4,5,6,7,9), 'H':(2,3,6,7,8,9),
    'I':(0,1,4,5,11,14), 'J':(2,3,4,5,6),
    'K':(6,7,8,12,13), 'L':(4,5,6,7), 'M':(2,3,6,7,10,12),
    'N':(2,3,6,7,10,13), 'O':(0,1,2,3,4,5,6,7),
    'P':(0,1,2,6,7,8,9), 'Q':(0,1,2,3,4,5,6,7,13),
    'R':(0,1,2,6,7,8,9,13), 'S':(0,1,7,8,9,3,4,5),
    'T':(0,1,11,14), 'U':(2,3,4,5,6,7), 'V':(6,7,12,15),
    'W':(2,3,6,7,13,15), 'X':(10,12,13,15),
    'Y':(10,12,14), 'Z':(0,1,4,5,12,15),
    '-':(8,9), '_':(4,5), '=':(8,9,4,5), '/':(12,15),
    '\\':(10,13), '+':(8,9,11,14), '*':(8,9,10,12,13,15),
    '?':(0,1,2,9,14), '!':(11,14), '.':(14,), ':':(11,14),
    '(':(12,13), ')':(10,15), '[':(0,5,6,7), ']':(1,2,3,4),
    '<':(12,13), '>':(10,15), "'":(11,), '"':(7,11),
}

def polygon(a,b):
    x,y=a;u,v=b;dx=u-x;dy=v-y;length=math.hypot(dx,dy)
    tx,ty=dx/length*5,dy/length*5
    nx,ny=-dy/length*7,dx/length*7
    return QPolygonF([QPointF(x,y),QPointF(x+tx+nx,y+ty+ny),
                     QPointF(u-tx+nx,v-ty+ny),QPointF(u,v),
                     QPointF(u-tx-nx,v-ty-ny),QPointF(x+tx-nx,y+ty-ny)])

class IIDXTicker(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.text=' '*9
        self.cached = QPixmap()
        self.shapes=[polygon(a,b) for a,b in SEGMENTS]
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAccessibleName('IIDX LED ticker')

    def set_text(self,data):
        if not isinstance(data,list) or not data or not isinstance(data[0],str):return
        value=data[0].replace('\x00',' ').upper()[:9].ljust(9)
        if value!=self.text:
            self.text=value
            self.cached = QPixmap()
            self.setAccessibleDescription(value)
            self.update()

    def paintEvent(self,event):
        if self.cached.isNull() or self.cached.size() != self.size():
            self.render_cache()
        p=QPainter(self)
        p.drawPixmap(0,0,self.cached)

    def render_cache(self):
        self.cached=QPixmap(self.size())
        self.cached.fill(Qt.transparent)
        p=QPainter(self.cached)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(),QColor('#101010'))
        p.setPen(QColor('#414148'));p.drawRect(self.rect().adjusted(0,0,-1,-1))
        p.setPen(Qt.NoPen)
        # Slant each cell like the mockup while keeping all nine cells visible.
        p.translate(20,10);p.scale((self.width()-40)/1260,(self.height()-20)/200)
        for index,char in enumerate(self.text):
            p.save();p.translate(index*140+20,0);p.shear(-.07,0)
            active=GLYPHS.get(char,GLYPHS['?'])
            for number,shape in enumerate(self.shapes):
                p.setBrush(QColor('#e00000') if number in active else QColor('#242424'))
                p.drawPolygon(shape)
            p.restore()

        p.end()
