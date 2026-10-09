import { useCallback, useEffect, useState } from "react";
import "./FullscreenExperience.css";

const CHOICE_KEY = "developer-os.fullscreen-choice.v1";

export default function FullscreenExperience() {
  const [promptOpen, setPromptOpen] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(Boolean(document.fullscreenElement));
  const [message, setMessage] = useState("");

  useEffect(() => {
    try {
      if (!window.localStorage.getItem(CHOICE_KEY) && document.fullscreenEnabled) {
        setPromptOpen(true);
      }
    } catch {
      if (document.fullscreenEnabled) setPromptOpen(true);
    }
    const sync = () => setIsFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", sync);
    return () => document.removeEventListener("fullscreenchange", sync);
  }, []);

  const remember = (choice) => {
    try { window.localStorage.setItem(CHOICE_KEY, choice); } catch { /* storage may be blocked */ }
    setPromptOpen(false);
  };

  const enterFullscreen = useCallback(async () => {
    setMessage("");
    try {
      if (!document.fullscreenElement) {
        const target = document.documentElement;
        if (!target?.requestFullscreen) throw new Error("Fullscreen is not supported by this browser.");
        await target.requestFullscreen({ navigationUI: "hide" });
      }
      remember("accepted");
    } catch (error) {
      setMessage(error?.message || "Fullscreen could not be enabled. Use your browser's fullscreen shortcut instead.");
    }
  }, []);

  const leaveFullscreen = useCallback(async () => {
    setMessage("");
    try {
      if (document.fullscreenElement && document.exitFullscreen) await document.exitFullscreen();
    } catch {
      setMessage("Your browser did not allow fullscreen to close. Use Esc to leave fullscreen.");
    }
  }, []);

  const resetPrompt = () => {
    try { window.localStorage.removeItem(CHOICE_KEY); } catch { /* storage may be blocked */ }
    setPromptOpen(true);
  };

  return (
    <>
      {!promptOpen && (
        <button className="dos-fullscreen-toggle" type="button"
          onClick={isFullscreen ? leaveFullscreen : resetPrompt}
          aria-label={isFullscreen ? "Exit fullscreen" : "Fullscreen options"}
          title={isFullscreen ? "Exit fullscreen (Esc)" : "Fullscreen options"}>
          {isFullscreen ? "⤢ EXIT FULLSCREEN" : "⛶ FULLSCREEN"}
        </button>
      )}
      {promptOpen && (
        <div className="dos-fullscreen-backdrop" role="presentation">
          <section className="dos-fullscreen-dialog" role="dialog" aria-modal="true" aria-labelledby="dos-fullscreen-title">
            <div className="dos-fullscreen-symbol" aria-hidden="true">⛶</div>
            <p className="dos-fullscreen-kicker">WORKSPACE DISPLAY</p>
            <h2 id="dos-fullscreen-title">Make Developer OS your full-screen workspace?</h2>
            <p className="dos-fullscreen-copy">Use the whole screen for your projects, dashboard and IDE. The layout will adapt to your display size. You can leave fullscreen any time with Esc.</p>
            {message && <p className="dos-fullscreen-error" role="status">{message}</p>}
            <div className="dos-fullscreen-actions">
              <button type="button" className="dos-fullscreen-primary" onClick={enterFullscreen}>ENTER FULLSCREEN</button>
              <button type="button" className="dos-fullscreen-secondary" onClick={() => remember("declined")}>KEEP WINDOWED</button>
            </div>
            <small>This is optional and can be changed any time using the fullscreen control.</small>
          </section>
        </div>
      )}
    </>
  );
}
