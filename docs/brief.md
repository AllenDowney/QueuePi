# QueuePi — Project brief

*The original brief, as received 2026-11-03. The working plan is `docs/plan.md`; decisions recorded there take precedence over this document.*

---

## Project overview

Create a new project called **QueuePi**, with the GitHub repository name `QueuePi` (CamelCase).

QueuePi is a lightweight, self-hosted order queue and pickup display for small restaurants, initially intended for a sandwich shop.

The problem: During busy periods, customers don't know how long they'll wait, repeatedly ask employees about their orders, and sometimes don't realize that the last two digits of their receipt number identify their order.

QueuePi provides a customer-facing monitor showing which orders are waiting and which are ready, plus simple phone-based controls for employees.

The system should be inexpensive, reliable, easy to operate during a lunch rush, and independent of cloud services.

## 1. Target hardware

The anticipated deployment is:

- Raspberry Pi 4, potentially with only 1 GB RAM
- Raspberry Pi OS Lite (64-bit)
- Fanless passive-cooling case
- 24-inch, 1920×1080 HDMI monitor
- Built-in Pi Wi-Fi
- Employee phones or a tablet accessing the application through their browsers

The Pi will both host the application and drive the HDMI monitor.

The application must also run on a conventional Linux desktop for development and testing.

Avoid requiring a desktop environment or Chromium on the Pi.

## 2. Proposed architecture

Use Python throughout the backend and display.

**Backend**

- FastAPI for the HTTP API and employee web interfaces
- SQLite for persistent order storage
- Simple HTML/CSS/JavaScript for employee interfaces
- No external JavaScript frameworks unless clearly justified
- No external CDN dependencies
- No cloud services

**Customer display**

- Pygame/SDL rendering directly to HDMI
- Full-screen 1920×1080 display
- Large, high-contrast text readable from several feet away
- Regular polling of the local backend for updates
- No Chromium or desktop environment required

**Deployment**

- systemd services for automatic startup and recovery
- Local network operation
- Optional configuration allowing the Pi to provide its own Wi-Fi access point
- No internet connection required for normal operation

The backend should be independent of the display renderer so either can be developed and tested separately.

Consider whether the Pygame display should consume the same read-only HTTP API used by other clients rather than accessing SQLite directly.

## 3. Order model

The shop already issues numbered receipts.

The customer-facing order number is the **last two digits of the receipt number**, from `00` through `99`.

Employees should enter these two digits directly. There is no need to integrate with the shop's point-of-sale system.

Each order should have:

- An internal unique identifier
- A two-digit display number
- Creation timestamp
- Current status
- Timestamp when marked ready
- Timestamp when marked picked up, if applicable

For the first version, the status model should be minimal:

- `waiting`
- `ready`
- `picked_up`

The last status can be represented as an archived order rather than an active display state.

Orders need not be completed in the order they were entered.

Prevent accidental duplicate active numbers while allowing receipt numbers to wrap around and be reused after earlier orders are completed.

Store timestamps so we can eventually measure both order preparation time and the interval between readiness and pickup.

## 4. Employee interfaces

Provide responsive web pages that work on ordinary Android and iPhone browsers.

### Register interface

Optimized for entering orders quickly.

- Large numeric input for a two-digit receipt number
- Prominent Add Order button
- Automatically return focus to the input after submission
- Preserve leading zeros
- Show clear confirmation that an order was added
- Prevent or warn about duplicate active numbers
- Allow correction of mistakes

The goal is to minimize interactions during busy periods.

### Kitchen interface

Show outstanding orders as large, touch-friendly buttons.

- Orders displayed in order of creation
- Tap an order to mark it Ready
- Ready orders shown separately
- Allow marking ready orders as Picked Up
- Allow undoing an accidental status change
- Handle simultaneous use by multiple employees

Avoid intermediate statuses such as Preparing unless testing demonstrates that they would be useful.

Consider whether the register and kitchen pages should share components or whether a single combined employee interface would be useful on a tablet.

## 5. Customer display

The customer-facing display should be simple, legible, and visually calm.

The most important elements are:

**Find your order number**

Prominently explain:

"Your order number is the last two digits of your receipt."

Include an example receipt number with the final two digits highlighted.

**Waiting**

Show the order numbers currently in the queue.

**Ready for pickup**

Show completed orders prominently, with larger numbers and a visually distinct color.

Use high contrast and a layout designed for a 24-inch monitor viewed from several feet away.

Consider how to handle a large queue without making text too small. For the prototype, implement a sensible overflow strategy without implying that orders are necessarily prepared in strict FIFO sequence.

Avoid completion-time estimates initially. We want to collect reliable observations before deciding whether estimates would help.

The display should update automatically without requiring employee interaction.

## 6. Reliability and operations

This system should behave like an appliance.

Requirements:

- Start automatically when the Pi boots
- Recover automatically from application crashes
- Preserve outstanding orders across restarts
- Work without internet access
- Be usable from multiple phones simultaneously
- Provide an easy end-of-day reset
- Allow correction of accidental actions
- Handle unexpected shutdowns reasonably
- Maintain a small local application log

The display should remain useful if the backend temporarily becomes unavailable, showing a clear connection warning rather than crashing or displaying misleading stale information.

Use SQLite transactions and appropriate constraints to maintain consistency.

Employee write operations should have lightweight authentication, such as a staff PIN or another practical mechanism. The customer display must not expose administrative controls.

Avoid unnecessary complexity: no user account system, cloud authentication, message broker, or external database server.

## 7. Testing

Develop and test on Linux before deploying to the Pi.

Include automated tests for:

- Adding orders
- Two-digit formatting and leading zeros
- Duplicate active numbers
- Legal and illegal status transitions
- Out-of-order completion
- Undo operations
- Order-number wraparound
- Simultaneous employee actions
- Recovery after restart
- End-of-day reset

The customer display should support a desktop/windowed development mode so we can inspect it without dedicated hardware.

Create a small simulation or fixture generator that populates realistic queues, including busy periods with 20–30 outstanding orders.

## 8. Project structure

Start with a conventional Python project, for example:

```
QueuePi/
├── README.md
├── pyproject.toml
├── LICENSE
├── .gitignore
├── src/
│   └── queuepi/
│       ├── __init__.py
│       ├── server.py
│       ├── database.py
│       ├── models.py
│       ├── display.py
│       ├── templates/
│       └── static/
├── tests/
├── scripts/
├── deploy/
│   └── systemd/
└── docs/
```

This is a suggestion, not a mandatory structure. Adjust if there's a simpler and more maintainable organization.

Prefer clear code and minimal dependencies over elaborate architecture.

Use type annotations where helpful, and document how to run the application locally.

## 9. Open-source considerations

The project is intended to be open source.

Working name: **QueuePi**

Suggested description:

"A lightweight, self-hosted order queue and pickup display for small restaurants."

The project should not depend on Raspberry Pi-specific APIs except for the actual display and deployment configuration.

Keep the software portable to other Linux computers.

Choose an appropriate permissive open-source license, subject to confirmation.

### Existing project for reference

Review:

[https://github.com/decisionsciencecorp/orderboard](https://github.com/decisionsciencecorp/orderboard)

This open-source Ghost Kitchen Order Board project has a related purpose.

Useful ideas include:

- High-contrast customer displays
- Simple order status transitions
- Separate administrative and public views
- SQLite persistence
- Automatic display refreshing
- Tests for the order lifecycle

It uses PHP and JavaScript and is licensed AGPLv3.

**Do not copy source code from this project.** We want an independently implemented, smaller application with a potentially permissive license.

## 10. Initial development milestones

### Milestone 1: Working local prototype

Implement:

- FastAPI backend
- SQLite order persistence
- Register web page
- Kitchen web page
- Basic Pygame customer display
- Automated tests for core order operations

All components should run on the development Linux desktop.

We should be able to open the register and kitchen interfaces in separate browser windows and see updates on the Pygame display.

### Milestone 2: Interface refinement

- Improve employee usability
- Refine customer display layout
- Simulate busy periods
- Add error handling and undo
- Test multiple simultaneous clients

### Milestone 3: Raspberry Pi deployment

- Raspberry Pi OS Lite
- Dependencies and installation procedure
- systemd service configuration
- Automatic full-screen display startup
- Wi-Fi connectivity
- Recovery and reliability testing

Consider using NetworkManager to configure a standalone Wi-Fi access point, but do not make that a requirement for initial development.

### Milestone 4: Pilot at the sandwich shop

- Observe how employees actually handle orders
- Minimize additional employee actions
- Test whether customers understand the display
- Evaluate whether queue visibility reduces interruptions
- Collect preparation-time measurements

Do not expand the scope until the basic workflow has been tested.

## 11. First task

Start by creating the project skeleton and implementing Milestone 1.

Before writing substantial code, briefly review the proposed architecture and identify any decisions that could materially affect simplicity, reliability, or Raspberry Pi compatibility.

In particular, evaluate whether Pygame with Raspberry Pi OS Lite is the best way to render directly to HDMI, including how it will work under the current Raspberry Pi OS graphics stack.

Do not assume that old framebuffer-based techniques remain supported.

If there is a substantially simpler or more reliable approach, explain the tradeoff before implementing it.

Then proceed with a minimal working end-to-end prototype.

Do not build a comprehensive restaurant management system. The goal is a focused, reliable application that does one small job well.

One implementation detail I'd especially want Claude to investigate is Pygame on Raspberry Pi OS Lite without a graphical desktop. The Linux graphics stack has evolved, and running SDL directly through DRM/KMS is possible but requires some care. A small Wayland compositor might turn out to be simpler and more reliable.

Otherwise, I think this is a well-contained project, and the hardware we've selected should be sufficient.
