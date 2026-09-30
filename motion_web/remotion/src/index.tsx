import React from "react";
import {AbsoluteFill, Composition, Img, interpolate, registerRoot, spring, staticFile, useCurrentFrame, useVideoConfig} from "remotion";

// Positions come from Python (app/motion_designs.py) so the editable Premiere/CapCut export
// puts each word exactly where it is drawn here.
type Text = {text: string; x: number; y: number; size: number; color: string; font: string; spacing?: number};
type Item = {label: string; image: string; photo_x: number; photo_y: number; text_x: number; text_y: number};
type Payload = {
  seconds: number; show_text: boolean; bg_inner: string; bg_outer: string; accent: string; ink: string;
  label_font: string; title_font: string; items?: Item[]; texts?: Text[]; background?: string;
};

const place = (t: Text): React.CSSProperties => ({
  position: "absolute", left: t.x, top: t.y, transform: "translate(-50%,-50%)", whiteSpace: "nowrap",
  fontSize: t.size, fontFamily: t.font, color: t.color, letterSpacing: t.spacing || 0,
});

// Short scenes (about a second) still show the whole move: timings shrink with the scene.
const pace = (p: Payload) => Math.min(1, (p.seconds || 4) / 4);

const Carousel: React.FC<{payload: Payload}> = ({payload: p}) => {
  const frame = useCurrentFrame() / pace(p);
  const {fps} = useVideoConfig();
  return (
    <AbsoluteFill style={{background: `radial-gradient(circle at 50% 35%, ${p.bg_inner}, ${p.bg_outer})`}}>
      <div style={{position: "absolute", left: 0, right: 0, top: 760, height: 26, background: "rgba(0,0,0,.35)",
                   boxShadow: "0 18px 30px rgba(0,0,0,.35)"}} />
      {(p.items || []).map((item, i) => {
        const pop = spring({frame: frame - 8 - i * 12, fps, config: {damping: 13}});
        const tagIn = spring({frame: frame - 20 - i * 12, fps, config: {damping: 18}});
        return (
          <React.Fragment key={i}>
            <div style={{position: "absolute", left: item.photo_x, top: item.photo_y, width: 360, height: 360,
                         transform: `translateY(${(1 - pop) * -500}px) rotate(${(1 - pop) * -8}deg)`,
                         borderRadius: 18, overflow: "hidden", boxShadow: "0 20px 40px rgba(0,0,0,.45)",
                         border: `6px solid ${p.accent}`}}>
              <Img src={staticFile(item.image)} style={{width: "100%", height: "100%", objectFit: "cover"}} />
            </div>
            {p.show_text && (
              <div style={{...place({text: item.label, x: item.text_x, y: item.text_y, size: 44, color: p.ink, font: p.label_font}),
                           opacity: tagIn, background: p.accent, padding: "6px 22px", borderRadius: 8}}>
                {item.label}
              </div>
            )}
          </React.Fragment>
        );
      })}
    </AbsoluteFill>
  );
};

const Newspaper: React.FC<{payload: Payload}> = ({payload: p}) => {
  const frame = useCurrentFrame() / pace(p);
  const {fps} = useVideoConfig();
  const spin = spring({frame, fps, config: {damping: 16, mass: 1.4}});
  return (
    <AbsoluteFill style={{background: "#1a1510"}}>
      {p.background && <Img src={staticFile(p.background)} style={{position: "absolute", width: "100%", height: "100%",
        objectFit: "cover", filter: "sepia(.6) blur(5px) brightness(.4)"}} />}
      <div style={{position: "absolute", left: 260, top: 150, width: 1400, height: 780, background: "#efe6d2",
                   boxShadow: "0 40px 90px rgba(0,0,0,.6)",
                   transform: `scale(${0.2 + 0.8 * spin}) rotate(${(1 - spin) * 720}deg)`}}>
        <div style={{position: "absolute", left: 60, right: 60, top: 170, borderTop: "4px double #2b241b"}} />
        <div style={{position: "absolute", left: 60, right: 60, top: 640, borderTop: "2px solid #2b241b"}} />
      </div>
      {p.show_text && (p.texts || []).map((t, i) => (
        <div key={i} style={{...place(t), opacity: interpolate(frame, [18 + i * 4, 30 + i * 4], [0, 1],
          {extrapolateLeft: "clamp", extrapolateRight: "clamp"})}}>{t.text}</div>
      ))}
    </AbsoluteFill>
  );
};

const meta = ({props}: {props: {payload: Payload}}) => ({durationInFrames: Math.max(1, Math.round((props.payload.seconds || 4) * 30))});
const empty = {payload: {seconds: 4, show_text: true, bg_inner: "#1e4a33", bg_outer: "#0b1a12", accent: "#f2c230", ink: "#1e180e",
  label_font: "Avenir Next", title_font: "Georgia"} as Payload};

const Root: React.FC = () => (
  <>
    <Composition id="Carousel" component={Carousel} width={1920} height={1080} fps={30} durationInFrames={120}
                 defaultProps={empty} calculateMetadata={meta} />
    <Composition id="Newspaper" component={Newspaper} width={1920} height={1080} fps={30} durationInFrames={120}
                 defaultProps={empty} calculateMetadata={meta} />
  </>
);

registerRoot(Root);
