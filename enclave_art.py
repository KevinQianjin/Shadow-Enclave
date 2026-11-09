"""Procedural compositions for the image carrier themes."""

import math
import random

from PIL import Image, ImageColor, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

SIZE = 1000
TAU = math.tau


def _gradient(top, bottom):
    return ImageOps.colorize(Image.linear_gradient("L").resize((SIZE, SIZE)), top, bottom)


def _font(size):
    for name in ("segoeuib.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


class Scene:
    def __init__(self, style):
        self.style = style
        self.focus = int(SIZE * style.focus)
        self.scale = style.scale
        self.rng = random.Random(style.theme)
        self.image = None
        self.draw = None

    def background(self, top, bottom=None):
        self.image = _gradient(top, bottom) if bottom else Image.new("RGB", (SIZE, SIZE), top)
        self.draw = ImageDraw.Draw(self.image, "RGBA")

    def accent(self, default):
        return ImageColor.getrgb(self.style.accent or default)

    def disk(self, x, y, radius, color):
        self.draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=color)

    def ridge(self, y, amp, freq, phase, color):
        points = [(x, y + amp * (0.58 * math.sin(x * freq + phase) +
                                 0.28 * math.sin(x * freq * 2.7) + 0.14 * math.cos(x * freq * 7.3)))
                  for x in range(0, SIZE + 1, 5)]
        self.draw.polygon([*points, (SIZE, SIZE), (0, SIZE)], fill=color)

    def stars(self, count, limit, color):
        for _ in range(count):
            x, y = self.rng.randrange(SIZE), self.rng.randrange(limit)
            self.disk(x, y, self.rng.choice((0.7, 1, 1, 1.5, 2.2)), (*color, self.rng.randint(50, 230)))


def twilight(scene):
    scene.background("#161734", "#D25951")
    hue = scene.accent("#FFD799")
    scene.stars(150, 410, (255, 233, 224))
    x = scene.focus + 120
    radius = 80 * scene.scale
    for i in range(9, 0, -1):
        scene.disk(x, 450, radius + i * 17, (*hue, 7))
    scene.disk(x, 450, radius, hue)
    for i, color in enumerate(("#A14C5E", "#6D3759", "#442747", "#271D37", "#181A2E")):
        scene.ridge(610 + 77*i, (113 - 12*i) * scene.scale, .008+i*.0017, i*1.23, color)


def aurora(scene):
    scene.background("#081D30", "#1F5265")
    scene.stars(240, 720, (209, 240, 239))
    green = scene.accent("#74FFBF")
    light = Image.new("RGBA", (SIZE, SIZE))
    brush = ImageDraw.Draw(light)
    offset = (scene.focus - 500) * .9
    for i in range(22):
        points = [(x, 215 + offset*.3 + i*5 + 102*scene.scale*math.sin(x*.005 + i*.045)
                   + 41*math.sin(x*.011 + offset*.004)) for x in range(0, SIZE + 1, 6)]
        brush.line(points, fill=(*green, 90 + i*3), width=3)
    glow = light.filter(ImageFilter.GaussianBlur(16))
    scene.image.paste(glow, mask=glow.getchannel("A"))
    scene.image.paste(light, mask=light.getchannel("A"))
    for i, shade in enumerate(("#7298A8", "#426577", "#203B50", "#102738")):
        scene.ridge(720 + i * 61, 68 - i*7, .014 + i*.002, i + 1, shade)
    scene.draw.polygon([(0, 960), (280, 837), (scene.focus, 894), (752, 826), (1000, 881), (1000, 1000), (0, 1000)],
                       fill="#BBD8D9")


def synth_grid(scene):
    scene.background("#190A32", "#862C68")
    x = scene.focus
    color = scene.accent("#FF975B")
    radius = 175 * scene.scale
    scene.disk(x, 427, radius, color)
    for y in range(370, 605, 18):
        distance = math.sqrt(max(0, radius**2 - (y-427)**2))
        scene.draw.line((x-distance, y, x+distance, y), fill="#9C4875", width=max(2,(y-350)//18))
    scene.draw.rectangle((0, 617, SIZE, SIZE), fill="#180F31")
    for i in range(-10, 11):
        scene.draw.line((x, 616, x + 180*i, SIZE), fill="#44DEFA", width=3)
    for i in range(1, 14):
        y = 617 + int((i/13)**2 * 380)
        scene.draw.line((0, y, SIZE, y), fill="#B651F0", width=2)
    scene.draw.line((0, 616, SIZE, 616), fill="#FF78CC", width=5)


def aerial_surf(scene):
    scene.background("#103C55", "#007E84")
    hue = scene.accent("#79E3CE")
    drift = scene.focus - 500
    for i in range(23, -1, -1):
        shore = [(x, 240 + 0.43*x + 105*scene.scale*math.sin(x*.004 + drift*.003) + i*12)
                 for x in range(0, SIZE + 1, 7)]
        if i == 23:
            scene.draw.polygon([*shore,(SIZE,SIZE),(0,SIZE)],fill="#E9C698")
        else:
            scene.draw.line(shore, fill=(*hue, 25+i*4), width=4)
    for i in range(420):
        x=scene.rng.randrange(SIZE)
        y=scene.rng.randrange(SIZE)
        if y < 240+0.43*x+105*scene.scale*math.sin(x*.004+drift*.003)+230:
            scene.draw.line((x,y,x+scene.rng.randint(3,24),y+7),fill=(201,255,238,scene.rng.randint(10,90)),width=2)


def blossom(scene):
    scene.background("#FBE4DC", "#C0ACC6")
    pink=scene.accent("#F19BAF")
    scene.disk(860, 195, 112, (255, 244, 230, 112))
    focus=scene.focus
    scene.draw.line([(-30,785),(238,630),(focus,431),(855,292),(1010,170)],fill="#463145",width=34)
    for i in range(11):
        x = (focus - 260 + i * 75) % 920
        y = 720 - .44*x
        tip=(x+scene.rng.randint(-127,139), y-scene.rng.randint(70,220)*scene.scale)
        scene.draw.line((x,y,*tip),fill="#64404E",width=7)
        for j in range(8):
            bx,by=tip[0]+scene.rng.randint(-40,40),tip[1]+scene.rng.randint(-50,50)
            r=scene.rng.randint(8,22)
            for petal in range(5):
                angle=petal*TAU/5
                scene.disk(bx+r*math.cos(angle),by+r*math.sin(angle),r*.7,(*pink,140+j*12))
            scene.disk(bx,by,r*.38,"#FFE8D6")
    for i in range(40):
        x,y=scene.rng.randrange(SIZE),scene.rng.randrange(SIZE)
        scene.draw.ellipse((x,y,x+scene.rng.randint(6,19),y+7),fill=(*pink,100))


def dunes(scene):
    scene.background("#A74431","#FFBF71")
    hue=scene.accent("#FFC47C")
    for i in range(7):
        base=170+i*127
        points=[(x,base+140*scene.scale*math.sin(x*.0028+i*.64+scene.focus*.004)+36*math.sin(x*.006))
                for x in range(0,SIZE+1,5)]
        scene.draw.polygon([*points,(SIZE,SIZE),(0,SIZE)],fill=("#D36840","#BA4D36","#E78147","#A53D36","#CB603C","#F6A358","#7F3132")[i])
        for step in range(4):
            scene.draw.line([(x,y+3+step*10) for x,y in points],fill=(*hue,130-step*23),width=3)


def ink_wash(scene):
    scene.background("#F5F1E4")
    charcoal=scene.accent("#353E3D")
    shift=(scene.focus-500)//3
    for i in range(7):
        x = 110+96*i+shift
        base=706+23*i
        peak=base-(210-14*i)*scene.scale
        points=[(x-190,base),(x-65,base-28),(x+2,peak),(x+60,base-51),(x+220,base)]
        scene.draw.polygon(points,fill=(*charcoal,23+i*13))
        scene.draw.line(points,fill=(*charcoal,26+i*12),width=3)
    for i in range(19):
        x=scene.rng.randint(40,800)
        y=scene.rng.randint(720,930)
        scene.draw.line((x,y,x+scene.rng.randint(8,130),y),fill=(*charcoal,40),width=2)
    scene.draw.rectangle((852,112,891,155),fill="#A64136")
    scene.draw.line((858,129,884,129,884,144,862,144,862,120),fill="#F5F1E4",width=3)


def nebula(scene):
    scene.background("#070718","#1E1039")
    scene.stars(280,SIZE,(190,210,250))
    hue=scene.accent("#B991FF")
    x0=scene.focus
    for i in range(11000):
        angle=scene.rng.uniform(0,9)
        spread=scene.rng.gauss(0,42)
        radius=(25+angle*44)*scene.scale
        arm=(i%2)*math.pi
        x=x0+math.cos(angle+arm)*radius+spread
        y=503+math.sin(angle+arm)*radius*.57+spread*.47
        light=scene.rng.randint(20,132)
        scene.disk(x,y,scene.rng.uniform(.5,2.9),(*hue,light))
    for i in range(13,0,-1):
        scene.disk(x0,503,i*3.7,(*hue,21))
    scene.disk(x0,503,10,"#F9E2FF")


def rainy_city(scene):
    scene.background("#111A32","#30415C")
    x=scene.focus
    hue=scene.accent("#48B8EE")
    horizon=445
    for side in (-1,1):
        edge=0 if side<0 else SIZE
        scene.draw.polygon([(edge,35),(x+side*65,horizon),(x+side*62,horizon+119),(edge,790)],
                           fill="#17223A")
        for k in range(7):
            depth=(k+1)/8
            wx=x+side*(80+depth*440)
            top=horizon-depth*430
            bottom=horizon+120+depth*225
            scene.draw.line((wx,top,wx,bottom),fill="#334868",width=4+int(depth*7))
            for row in range(6):
                y=top+row*(19+depth*33)
                scene.draw.line((wx,y,wx-side*(17+depth*30),y+2),fill=(*hue,125+row*14),width=3)
    scene.draw.polygon([(x-47,558),(x+47,558),(SIZE,1000),(0,1000)],fill="#202E47")
    for j in range(115):
        y=scene.rng.randint(566,999)
        half=(y-550)*1.0
        px=x+scene.rng.uniform(-half,half)
        scene.draw.line((px,y,px+scene.rng.randint(5,58),y),fill=(*hue,scene.rng.randint(25,150)),width=2)
    for i in range(165):
        px,py=scene.rng.randrange(SIZE),scene.rng.randrange(SIZE)
        drop=scene.rng.randint(12,48)*scene.scale
        scene.draw.line((px,py,px-drop*.23,py+drop),fill=(199,224,255,scene.rng.randint(22,100)),width=2)


def woodcut(scene):
    scene.background("#F2DFB7")
    hue=scene.accent("#C85D46")
    scene.draw.rectangle((48,48,952,952),outline="#38435B",width=4)
    scene.disk(770,285,67*scene.scale,hue)
    scene.draw.polygon([(655,575),(756,434),(848,575)],fill="#9A8B94")
    scene.draw.polygon([(721,484),(756,434),(792,486),(756,470)],fill="#F7EDD9")
    center=scene.focus*.5-220
    for i, color in enumerate(("#1B3E60","#255A76","#368698","#81AFA9")):
        points=[(x, 742+i*38-360*scene.scale*math.exp(-((x-center-210-i*66)/173)**2)
                    + 36*math.sin(x*.017+i))
                for x in range(51,951,4)]
        scene.draw.polygon([*points,(950,949),(51,949)],fill=color)
        scene.draw.line(points,fill="#FCF0CF",width=7)
        for j in range(22):
            px, py=points[scene.rng.randrange(len(points)//2)]
            scene.disk(px+scene.rng.randint(-13,13),py-scene.rng.randint(6,38),2,"#F8E7C4")


def herbarium(scene):
    scene.background("#F2F0DF")
    leaf=scene.accent("#407054")
    scene.draw.rectangle((62,62,938,938),outline="#B8B69B",width=3)
    x0=scene.focus
    for branch in range(-3,4):
        bend=branch*108*scene.scale
        top=177+abs(branch)*55
        pts=[(x0+bend*(t/40)**1.15,838+(top-838)*t/40) for t in range(41)]
        scene.draw.line(pts,fill="#526A50",width=5)
        for i in range(6,40,5):
            x,y=pts[i]
            side=(-1 if i%2 else 1)
            wide=(56+5*abs(branch))*scene.scale
            end=(x+side*wide,y-49)
            shape=[(x,y),(x+side*wide*.35,y-39),end,(x+side*wide*.58,y-8)]
            scene.draw.polygon(shape,fill=(*leaf,95+i*3),outline="#50765C",width=2)
            scene.draw.line((x,y,*end),fill="#B9C3A0",width=2)
    scene.draw.text((105,874),"FIELD STUDY  /  BOTANICAL",font=_font(25),fill="#435949")


def travel_poster(scene):
    scene.background("#F4E7C9")
    hue=scene.accent("#DB7350")
    scene.draw.rectangle((62,66,938,762),fill="#EDB166")
    scene.disk(scene.focus,365,123*scene.scale,hue)
    scene.draw.polygon([(62,684),(318,360),(453,534),(607,451),(938,721),(938,766),(62,766)],fill="#285B5C")
    scene.draw.polygon([(62,748),(285,593),(412,642),(609,560),(938,722),(938,766),(62,766)],fill="#213F4A")
    scene.draw.text((75,790),"OUT WEST",font=_font(85),fill="#233E44")
    scene.draw.line((78,902,922,902),fill=hue,width=5)
    scene.draw.text((81,920),"THE OPEN COUNTRY",font=_font(24),fill="#233E44")


def geometric(scene):
    scene.background("#FAE9CA")
    palette=(scene.accent("#EB573D"),"#183A57","#F2B544","#4D958A")
    x=scene.focus
    r=165*scene.scale
    scene.draw.polygon([(45,105),(x+210,199),(x-180,608)],fill=palette[1])
    scene.draw.rectangle((67,663,591,940),fill=palette[2])
    scene.disk(x+146,431,r,palette[0])
    scene.draw.polygon([(x+95,493),(965,718),(775,952)],fill=palette[3])
    for y in range(135,910,34):
        scene.draw.line((795,y,914,y+89),fill="#1D3244",width=5)
    scene.draw.ellipse((157,363,439,645),outline="#FAE9CA",width=14)


def architecture(scene):
    scene.background("#D9E0E0","#B4BFC4")
    hue=scene.accent("#304450")
    x=scene.focus
    a=190*scene.scale
    scene.draw.polygon([(0,0),(x,299),(x,1000),(0,1000)],fill="#8E9EA6")
    scene.draw.polygon([(x,299),(1000,38),(1000,1000),(x,1000)],fill="#334752")
    scene.draw.polygon([(x+a,352),(1000,157),(1000,1000),(x+a,892)],fill=hue)
    for y in range(0,1050,55):
        scene.draw.line((0,y,x,299+(y*.7)),fill="#4C626C",width=3)
        scene.draw.line((x,299+y*.68,1000,44+y),fill="#90A2AA",width=3)
    for i in range(1,12):
        x1=x+(1000-x)*i/12
        y1=299-(x1-x)*.52
        scene.draw.line((x1,y1,x1,1000),fill="#182D38",width=4)


RENDERERS = {
    "sunset": twilight, "emerald": aurora, "cyber": synth_grid, "ocean": aerial_surf,
    "sakura": blossom, "desert": dunes, "ink": ink_wash, "nebula": nebula,
    "rain_city": rainy_city, "ukiyo": woodcut, "botanical": herbarium, "poster": travel_poster,
    "geometry": geometric, "architecture": architecture,
}


def render_scene(style, width, height):
    scene = Scene(style)
    RENDERERS[style.theme](scene)
    for _ in range(int(22000 * style.texture)):
        x, y = scene.rng.randrange(SIZE), scene.rng.randrange(SIZE)
        tone = 255 if scene.rng.random() < .5 else 20
        scene.draw.point((x, y), fill=(tone, tone, tone, 50))
    image = ImageEnhance.Brightness(scene.image).enhance(style.light)
    return image.resize((width, height), Image.Resampling.LANCZOS)
