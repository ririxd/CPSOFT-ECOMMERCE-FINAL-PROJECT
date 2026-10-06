ART HOUSE
=========

Art House is a storefront concept for discovering independent art and prints.
The static home page includes an artwork collection, artist introduction,
newsletter signup, search overlay, and interactive shopping bag preview.

RUN LOCALLY
-----------

Open index.html in a browser or run a static web server from this folder:

    py -m http.server 8000

Then visit http://localhost:8000. Bootstrap, Swiper, and Google Fonts load from
public CDNs and need an internet connection.

PROJECT FILES
-------------

index.html       Page markup and lightweight storefront interactions
styles.css       Base styles, preserved template effects, and Art House design
css/vendor.css   Vendor styles
js/script.js     Existing page interactions and slider setup
js/plugins.js    Existing vendor plugins

CUSTOMIZE
---------

The four featured artwork cards are in the #artwork-grid section of index.html.
Replace their inline SVG artwork, titles, sizes, and prices with the shop's catalog.
Update the artist and story sections, newsletter destination, and contact links
with the shop's information.

The bag currently works as a front-end preview only. Product and quantity
selection are kept in the current browser session; checkout, inventory,
subscriptions, and payment processing need to be connected to a store service.
