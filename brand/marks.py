"""Regenerates the brand marks. One-off; needs Pillow, which is deliberately
not in requirements.txt — it would go into the Lambda zip for nothing.

    pip install pillow && python brand/marks.py
"""
"""My Sign identity. One geometry definition -> SVG vectors + PNG rasters."""
import os

from PIL import Image, ImageDraw

INK="#0B0C16"; GOLD="#D9A94E"; GOLD_HI="#EFC97E"; PAPER="#F1F0F4"
OUT=os.path.dirname(os.path.abspath(__file__))

# ---- the sparkle: four points, concave waists, vertical axis slightly longer ----
# quarter segment top -> right, mirrored into the other three
def sparkle_path(cx=60, cy=60, rv=57, rh=49, waist=0.86, tight=0.026):
    """rv/rh = vertical/horizontal reach. waist pulls the curve toward centre."""
    def seg(a, b):
        # a,b are (angle_deg) tips; build cubic from tip a to tip b bowing inward
        pass
    tips = [(cx, cy-rv), (cx+rh, cy), (cx, cy+rv), (cx-rh, cy)]
    d = f"M {tips[0][0]:.2f} {tips[0][1]:.2f} "
    order = [(0,1),(1,2),(2,3),(3,0)]
    for i,(A,B) in enumerate(order):
        ax,ay = tips[A]; bx,by = tips[B]
        # control 1 hugs the axis of tip A, control 2 hugs the axis of tip B
        if A % 2 == 0:   # A is vertical tip
            c1 = (ax + (bx-ax)*tight, ay + (by-ay)*waist)
            c2 = (bx + (ax-bx)*waist, by + (ay-by)*tight)
        else:            # A is horizontal tip
            c1 = (ax + (bx-ax)*waist, ay + (by-ay)*tight)
            c2 = (bx + (ax-bx)*tight, by + (ay-by)*waist)
        d += f"C {c1[0]:.2f} {c1[1]:.2f}, {c2[0]:.2f} {c2[1]:.2f}, {bx:.2f} {by:.2f} "
    return d + "Z", tips

def cubic(p0,p1,p2,p3,n=48):
    pts=[]
    for i in range(n+1):
        t=i/n; u=1-t
        x=u**3*p0[0]+3*u*u*t*p1[0]+3*u*t*t*p2[0]+t**3*p3[0]
        y=u**3*p0[1]+3*u*u*t*p1[1]+3*u*t*t*p2[1]+t**3*p3[1]
        pts.append((x,y))
    return pts

def sparkle_poly(cx=60, cy=60, rv=57, rh=49, waist=0.86, tight=0.026):
    tips=[(cx,cy-rv),(cx+rh,cy),(cx,cy+rv),(cx-rh,cy)]
    poly=[]
    for A,B in [(0,1),(1,2),(2,3),(3,0)]:
        ax,ay=tips[A]; bx,by=tips[B]
        if A%2==0:
            c1=(ax+(bx-ax)*tight, ay+(by-ay)*waist); c2=(bx+(ax-bx)*waist, by+(ay-by)*tight)
        else:
            c1=(ax+(bx-ax)*waist, ay+(by-ay)*tight); c2=(bx+(ax-bx)*tight, by+(ay-by)*waist)
        poly += cubic((ax,ay),c1,c2,(bx,by))[:-1]
    return poly

PATH_MAIN,_ = sparkle_path()
PATH_MINI,_ = sparkle_path(cx=97, cy=27, rv=17, rh=15.5)

# ------------------------------------------------------------------ SVG out
GRAD = ('<defs><linearGradient id="g" x1="0" y1="0" x2="0.35" y2="1">'
        f'<stop offset="0" stop-color="{GOLD_HI}"/>'
        f'<stop offset="0.55" stop-color="{GOLD}"/>'
        f'<stop offset="1" stop-color="#B8842C"/></linearGradient></defs>')

def svg(body, size=120, bg=None):
    b=f'<rect width="{size}" height="{size}" fill="{bg}"/>' if bg else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
            f'width="{size}" height="{size}">{GRAD}{b}{body}</svg>')

MARKS={}

MARKS["mark-sparkle"] = svg(
  f'<path d="{PATH_MAIN}" fill="url(#g)"/><path d="{PATH_MINI}" fill="{GOLD_HI}" opacity=".9"/>')

MARKS["mark-sparkle-solo"] = svg(f'<path d="{PATH_MAIN}" fill="url(#g)"/>')

ticks="".join(
  f'<line x1="60" y1="{7.5 if i%3==0 else 9.5}" x2="60" y2="{13.5}" stroke="{GOLD}" '
  f'stroke-width="{1.4 if i%3==0 else .9}" opacity="{.95 if i%3==0 else .5}" '
  f'transform="rotate({i*15} 60 60)"/>' for i in range(24))
MARKS["mark-seal"] = svg(
  f'<circle cx="60" cy="60" r="53" fill="none" stroke="{GOLD}" stroke-width="1.6"/>'
  f'{ticks}'
  f'<path d="{sparkle_path(rv=34,rh=31)[0]}" fill="{GOLD}"/>')

MARKS["mark-ascendant"] = svg(
  f'<path d="{sparkle_path(cy=52,rv=40,rh=37)[0]}" fill="{GOLD}"/>'
  f'<line x1="6" y1="86" x2="114" y2="86" stroke="{GOLD}" stroke-width="1.6" opacity=".85"/>'
  f'<line x1="6" y1="86" x2="34" y2="86" stroke="{GOLD}" stroke-width="1.6"/>'
  f'<line x1="86" y1="86" x2="114" y2="86" stroke="{GOLD}" stroke-width="1.6"/>')

MARKS["mark-negative"] = svg(
  f'<circle cx="60" cy="60" r="58" fill="{GOLD}"/>'
  f'<path d="{sparkle_path(rv=41,rh=38)[0]}" fill="{INK}"/>')

MARKS["mark-conjunction"] = svg(
  f'<path d="{sparkle_path(cx=46,cy=60,rv=48,rh=44)[0]}" fill="{GOLD}"/>'
  f'<circle cx="96" cy="34" r="7.5" fill="none" stroke="{GOLD}" stroke-width="1.6" opacity=".9"/>'
  f'<circle cx="96" cy="34" r="2.6" fill="{GOLD}"/>')

for name, svg in MARKS.items():
    with open(os.path.join(OUT, name + ".svg"), "w") as fh:
        fh.write(svg)

# ------------------------------------------------------------- PNG app icons
def hexrgb(h): h=h.lstrip("#"); return tuple(int(h[i:i+2],16) for i in (0,2,4))

def icon(size, squircle=True, ss=4):
    S=size*ss
    im=Image.new("RGBA",(S,S),(0,0,0,0)); d=ImageDraw.Draw(im)
    r=int(S*0.2237)                      # iOS superellipse approximation
    if squircle: d.rounded_rectangle([0,0,S-1,S-1],r,fill=hexrgb(INK))
    else:        d.ellipse([0,0,S-1,S-1],fill=hexrgb(INK))
    k=S/120.0
    grad=Image.new("RGB",(S,S)); gp=grad.load()
    a,b_=hexrgb(GOLD_HI),hexrgb("#B8842C")
    for yy in range(S):
        f=yy/(S-1); col=tuple(int(a[i]+(b_[i]-a[i])*f) for i in range(3))
        for xx in range(S): gp[xx,yy]=col
    mask=Image.new("L",(S,S),0); md=ImageDraw.Draw(mask)
    md.polygon([(x*k,y*k) for x,y in sparkle_poly(rv=45,rh=39)],fill=255)
    im.paste(grad,(0,0),mask)
    d.polygon([(x*k,y*k) for x,y in sparkle_poly(cx=93,cy=30,rv=14,rh=12)],fill=hexrgb(GOLD_HI))
    return im.resize((size,size),Image.LANCZOS)

for s in (1024,512,180,120):
    icon(s).save(os.path.join(OUT,f"appicon-{s}.png"))
icon(1024,squircle=False).save(os.path.join(OUT,"avatar-1024.png"))
print("wrote", len(MARKS),"svg +", 5, "png")
