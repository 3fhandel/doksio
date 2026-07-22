(() => {
  const SMART_DATE_SELECTOR = "input[data-smart-date='true']";

  function pad(value) {
    return String(value).padStart(2, "0");
  }

  function localToday() {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), now.getDate());
  }

  function formatIso(date) {
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  }

  function formatGerman(date) {
    return `${pad(date.getDate())}.${pad(date.getMonth() + 1)}.${date.getFullYear()}`;
  }

  function dateFromParts(year, month, day) {
    const date = new Date(year, month - 1, day);
    if (
      date.getFullYear() !== year ||
      date.getMonth() !== month - 1 ||
      date.getDate() !== day
    ) {
      return null;
    }
    return date;
  }

  function expandYear(value) {
    if (value >= 100) {
      return value;
    }
    return value >= 70 ? 1900 + value : 2000 + value;
  }

  function addRelative(baseDate, amount, unit) {
    const date = new Date(baseDate);
    if (["d", "day", "days", "tag", "tage"].includes(unit)) {
      date.setDate(date.getDate() + amount);
    } else if (["w", "week", "weeks", "woche", "wochen"].includes(unit)) {
      date.setDate(date.getDate() + amount * 7);
    } else if (["m", "month", "months", "monat", "monate"].includes(unit)) {
      const originalDay = date.getDate();
      date.setDate(1);
      date.setMonth(date.getMonth() + amount);
      const lastDay = new Date(date.getFullYear(), date.getMonth() + 1, 0).getDate();
      date.setDate(Math.min(originalDay, lastDay));
    } else if (["y", "year", "years", "jahr", "jahre"].includes(unit)) {
      const originalMonth = date.getMonth();
      date.setFullYear(date.getFullYear() + amount);
      if (date.getMonth() !== originalMonth) {
        date.setDate(0);
      }
    } else {
      return null;
    }
    return date;
  }

  function parseSmartDate(rawValue) {
    const value = rawValue.trim().toLowerCase();
    if (!value) {
      return "";
    }

    const today = localToday();
    if (["now", "today", "heute"].includes(value)) {
      return formatIso(today);
    }
    if (["tomorrow", "morgen"].includes(value)) {
      return formatIso(addRelative(today, 1, "day"));
    }
    if (["yesterday", "gestern"].includes(value)) {
      return formatIso(addRelative(today, -1, "day"));
    }

    const relativeMatch = value.match(
      /^([+-])\s*(\d+)\s*(d|day|days|tag|tage|w|week|weeks|woche|wochen|m|month|months|monat|monate|y|year|years|jahr|jahre)$/,
    );
    if (relativeMatch) {
      const sign = relativeMatch[1] === "-" ? -1 : 1;
      const amount = Number(relativeMatch[2]) * sign;
      const date = addRelative(today, amount, relativeMatch[3]);
      return date ? formatIso(date) : null;
    }

    const isoMatch = value.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
    if (isoMatch) {
      const date = dateFromParts(
        Number(isoMatch[1]),
        Number(isoMatch[2]),
        Number(isoMatch[3]),
      );
      return date ? formatIso(date) : null;
    }

    const separatedMatch = value.match(/^(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2}|\d{4}))?$/);
    if (separatedMatch) {
      const year = separatedMatch[3]
        ? expandYear(Number(separatedMatch[3]))
        : today.getFullYear();
      const date = dateFromParts(
        year,
        Number(separatedMatch[2]),
        Number(separatedMatch[1]),
      );
      return date ? formatIso(date) : null;
    }

    const numericMatch = value.match(/^\d{1,8}$/);
    if (numericMatch) {
      if (value.length <= 2) {
        const date = dateFromParts(
          today.getFullYear(),
          today.getMonth() + 1,
          Number(value),
        );
        return date ? formatIso(date) : null;
      }
      if (value.length === 3 || value.length === 4) {
        const day = Number(value.slice(0, value.length - 2));
        const month = Number(value.slice(-2));
        const date = dateFromParts(today.getFullYear(), month, day);
        return date ? formatIso(date) : null;
      }
      if (value.length === 5 || value.length === 6) {
        const day = Number(value.slice(0, value.length - 4));
        const month = Number(value.slice(value.length - 4, value.length - 2));
        const year = expandYear(Number(value.slice(-2)));
        const date = dateFromParts(year, month, day);
        return date ? formatIso(date) : null;
      }
      if (value.length === 7 || value.length === 8) {
        const day = Number(value.slice(0, value.length - 6));
        const month = Number(value.slice(value.length - 6, value.length - 4));
        const year = Number(value.slice(-4));
        const date = dateFromParts(year, month, day);
        return date ? formatIso(date) : null;
      }
    }

    return null;
  }

  function normalizeInput(input) {
    const value = input.value.trim();
    if (!value) {
      input.setCustomValidity("");
      input.classList.remove("is-invalid");
      return true;
    }
    const parsed = parseSmartDate(value);
    if (parsed === null) {
      input.setCustomValidity(
        "Bitte gib ein Datum ein, z. B. 23, 2307, 230726, today oder +1week.",
      );
      input.classList.add("is-invalid");
      return false;
    }
    const parsedDate = new Date(`${parsed}T00:00:00`);
    input.value = formatGerman(parsedDate);
    input.setCustomValidity("");
    input.classList.remove("is-invalid");
    return true;
  }

  function setupInput(input) {
    input.autocomplete = input.autocomplete || "off";
    input.addEventListener("blur", () => normalizeInput(input));
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        normalizeInput(input);
      }
    });
    input.form?.addEventListener("submit", () => normalizeInput(input));
  }

  document.querySelectorAll(SMART_DATE_SELECTOR).forEach(setupInput);

  window.doksioSmartDates = {
    parse: parseSmartDate,
    normalizeInput,
  };
})();
