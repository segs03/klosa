

let tab = document.querySelectorAll('.tab');
let slider = document.querySelector('.slider');
let bal = document.querySelector('#bal');
let visible = document.querySelector('#visible');
const pages = document.querySelectorAll(".tab-page");
let bankCard = document.querySelector("#bank_card");

let acct_btn = document.querySelectorAll(".acct");
let rsi= 0;


function initCardDeck() {
  const deck = document.getElementById("cardDeck");
  const hint = document.getElementById("deckHint");
  if (!deck) return;
  if (deck._layout) { deck._layout(); return; }

  const cards = [...deck.querySelectorAll(".bank-card-view")];
  if (!cards.length) return;
  const n = cards.length;
  const half = Math.floor(n / 2);
  const SIDE = Math.min(2, half);   // neighbours shown on each side
  const STEP = 0.08;                // how much smaller each step away gets
  const BOTTOM = 170;               // space kept for hint + bottom nav
  const MAX_SHOW = 0.5;             // max share of a card height that peeks out
  let cur = 0, pad = 0;

  function layout() {
    const h = cards[0].offsetHeight || 210;
    const top = deck.getBoundingClientRect().top;
    const avail = Math.max(h, window.innerHeight - top - BOTTOM);

    // visible strip of each neighbour, sized to fill the free space
    const v = SIDE
      ? Math.max(40, Math.min(h * MAX_SHOW, (avail - h) / (2 * SIDE)))
      : 0;
    pad = v * SIDE;

    cards.forEach((c, i) => {
      const o = ((i - cur + n + half) % n) - half;     // -2..+2 around the active card
      const a = Math.abs(o);
      const s = 1 - STEP * a;
      const y = o === 0 ? 0 : Math.sign(o) * (h / 2 + v * a - (h * s) / 2);

      c.style.position = "absolute";
      c.style.top = pad + "px";
      c.style.left = "0";
      c.style.width = "100%";
      c.style.margin = "0";
      c.style.pointerEvents = "none";
      c.style.transformOrigin = "center center";
      c.style.transition = "transform .45s cubic-bezier(.3,.8,.3,1), opacity .45s, filter .45s";
      c.style.zIndex = 10 - a;
      c.style.transform = `translateY(${y}px) scale(${s})`;
      c.style.opacity = a === 0 ? 1 : a === 1 ? 0.9 : a === 2 ? 0.6 : 0;
      c.style.filter = `brightness(${1 - 0.15 * a})`;
    });

    deck.style.position = "relative";
    deck.style.height = h + pad * 2 + "px";
    if (hint) hint.textContent = n > 1
      ? `Card ${cur + 1} of ${n}. Tap or swipe to change cards.`
      : "";
  }

  function move(dir) {
    if (n < 2) return;
    cur = (cur + dir + n) % n;
    layout();
  }

  let startY = 0, swipedAt = 0;
  deck.addEventListener("touchstart", (e) => { startY = e.touches[0].clientY; }, { passive: true });
  deck.addEventListener("touchend", (e) => {
    const dy = startY - e.changedTouches[0].clientY;
    if (Math.abs(dy) > 40) { swipedAt = Date.now(); move(dy > 0 ? 1 : -1); }
  }, { passive: true });

  deck.addEventListener("click", (e) => {
    if (Date.now() - swipedAt < 400) return;
    const y = e.clientY - deck.getBoundingClientRect().top;
    move(y < pad ? -1 : 1);       // top strip = previous, everything else = next
  });
  deck.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
    if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
  });

  window.addEventListener("resize", layout);
  deck.style.cursor = "pointer";
  deck._layout = layout;
  layout();
}


if (acct_btn.length) {

    let acct_slider = document.querySelector(".acct_slider");

    function acctSlider(animate = true) {
        let btn = acct_btn[rsi];

        if (!btn || !acct_slider) return;

        let x = btn.offsetLeft - acct_slider.offsetLeft;

        acct_slider.style.transition = animate
            ? "transform 0.4s ease, width 0.4s ease"
            : "none";

        acct_slider.style.width = `${btn.offsetWidth}px`;
        acct_slider.style.transform = `translateX(${x}px)`;
    }

    acct_btn.forEach((e, i) => {
        e.addEventListener("click", () => {
            rsi = i;
            acctSlider(true);
        });
    });
}

// stacked card click script
function openCardPage(){
// remove both card and tab active class
pages.forEach(p => p.classList.remove('active'));
tab.forEach(t => t.classList.remove('active'));

// add both tab and card active class
pages[1].classList.add('active');
tab[2].classList.add('active');
moveSlider(2);
requestAnimationFrame(() => initCardDeck());
}

// bank click script
bankCard.addEventListener("click", openCardPage );

// tab slider script
function moveSlider(index) {
    tab.forEach(t => t.classList.remove("active"));
    tab[index].classList.add("active");

    slider.style.transform = `translateX(${index * 100}%)`;
}

// tab script
tab.forEach((e, i) => {
    e.addEventListener('click', (t) => {
    
    const name = e.dataset.tab;

        // Show only the selected page
        pages.forEach(page => {
         page.classList.toggle(
                "active",
                page.id === `${name}-page`
            );
        });
        
        if(name === "recipient"){
         acctSlider(false);
        } 
    requestAnimationFrame(() => initCardDeck());
    moveSlider(i);
});
});

moveSlider(1);


// balance hiding script
if(bal){
let [whole, decimal] = bal.textContent.trim().split('.');
let balance  = bal.innerHTML = `${whole}. <small class='decimal'>${decimal}</small>`;
decimal !== undefined ? balance  : '';

let hidden = false;

visible.addEventListener('click', () => {
hidden = !hidden;

bal[hidden ? "textContent" : "innerHTML"] =  hidden ? "₦*****" :  balance;

visible.classList.toggle("fa-eye", hidden);
visible.classList.toggle('fa-eye-slash', !hidden);

})
}


// transaction and recipient panel script 
const panel = document.querySelectorAll(".trns");

panel.forEach((p, index) => {

    const handle = p.querySelector(".drag-handle");

    let startY = 0;
    let startTop = 0;
    let currentTop = 0;
    let dragging = false;
    let expanded = false;

    handle.addEventListener("pointerdown", (e) => {
        if (e.pointerType === "mouse" && e.button !== 0) return;

        dragging = true;
        startY = e.clientY;

        const rect = p.getBoundingClientRect();

        startTop = rect.top;
        currentTop = rect.top;

        Object.assign(p.style, {
            position: "fixed",
            top: `${rect.top}px`,
            left: `${rect.left}px`,
            width: `${rect.width}px`,
            height: `${rect.height}px`,
            right: "auto",
            bottom: "auto",
            margin: "0",
            boxSizing: "border-box",
            zIndex: "1000",
            transition: "none",
            transform: "none"
        });

        handle.setPointerCapture(e.pointerId);
    });

    handle.addEventListener("pointermove", (e) => {

        if (!dragging) return;

        const distance = e.clientY - startY;

        currentTop = Math.max(
            0,
            Math.min(window.innerHeight - 120, startTop + distance)
        );

        p.style.top = `${currentTop}px`;
        p.style.height = `${window.innerHeight - currentTop}px`;

        // Expand content as soon as the panel moves upward
        if (index === 0) {
            const shouldExpand = currentTop < window.innerHeight * 0.85;

            if (shouldExpand !== expanded) {
                expanded = shouldExpand;
                p.classList.toggle("expanded", expanded);
            }
        }
    });

    function endDrag() {
        if (!dragging) return;

        dragging = false;

        expanded = currentTop < window.innerHeight * 0.65;

        p.classList.toggle("expanded", expanded);

        p.style.transition =
            "top 0.25s ease, height 0.25s ease, border-radius 0.25s ease";

        if (expanded) {
            Object.assign(p.style, {
                top: "0",
                left: "0",
                width: "100%",
                height: "100dvh",
                borderRadius: "0",
                padding: "20px"
            });
        } else {
            p.classList.remove("expanded");
            p.removeAttribute("style");
        }
    }

    handle.addEventListener("pointerup", endDrag);
    handle.addEventListener("pointercancel", endDrag);

});

