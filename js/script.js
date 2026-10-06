(function($) {

    "use strict";
  
    var searchPopup = function () {
      // open search box
      $('.navbar').on('click', '.search-button', function (e) {
        $('.search-popup').toggleClass('is-visible');
      });
  
      $('.navbar').on('click', '.btn-close-search', function (e) {
        $('.search-popup').toggleClass('is-visible');
      });
  
      $(".search-popup-trigger").on("click", function (b) {
        b.preventDefault();
        $(".search-popup").addClass("is-visible"),
          setTimeout(function () {
            $(".search-popup").find("#search-popup").focus()
          }, 350)
      }),
        $(".search-popup").on("click", function (b) {
          ($(b.target).is(".search-popup-close") || $(b.target).is(".search-popup-close svg") || $(b.target).is(".search-popup-close path") || $(b.target).is(".search-popup")) && (b.preventDefault(),
            $(this).removeClass("is-visible"))
        }),
        $(document).keyup(function (b) {
          "27" === b.which && $(".search-popup").removeClass("is-visible")
        })
    }

    var initProductQty = function () {

      $('.product-qty').each(function () {
  
        var $el_product = $(this);
        var quantity = 0;
  
        $el_product.find('.quantity-right-plus').click(function (e) {
          e.preventDefault();
          var quantity = parseInt($el_product.find('#quantity').val());
          $el_product.find('#quantity').val(quantity + 1);
        });
  
        $el_product.find('.quantity-left-minus').click(function (e) {
          e.preventDefault();
          var quantity = parseInt($el_product.find('#quantity').val());
          if (quantity > 0) {
            $el_product.find('#quantity').val(quantity - 1);
          }
        });
  
      });
  
    } 

    $(document).ready(function() {

      searchPopup();
      
      /* Video */
      var $videoSrc;  
        $('.play-btn').click(function() {
          $videoSrc = $(this).data( "src" );
        });

        $('#myModal').on('shown.bs.modal', function (e) {

        $("#video").attr('src',$videoSrc + "?autoplay=1&amp;modestbranding=1&amp;showinfo=0" ); 
      })

      $('#myModal').on('hide.bs.modal', function (e) {
        $("#video").attr('src',$videoSrc); 
      })
        
      
      var swiper = new Swiper(".mySwiper", {
        slidesPerView: 3,
        spaceBetween: 10,
        pagination: {
          el: ".new-arrival-pagination",
          clickable: true,
        },
        navigation: {
          nextEl: ".main-slider-button-next",
          prevEl: ".main-slider-button-prev",
        },
        breakpoints: {
          0: {
            slidesPerView: 1,
          },
          572: {
            slidesPerView: 2,
          },
          1024: {
            slidesPerView: 3,
          },
          1280: {
            slidesPerView: 3,
          },
        },
      });

      var swiper = new Swiper(".testimonial-Swiper", {
        slidesPerView: 3,
        spaceBetween: 30,
        loop:true,
        pagination: {
          el: ".testimonial-pagination",
          clickable: true,
        },
        breakpoints: {
          0: {
            slidesPerView: 1,
          },
          572: {
            slidesPerView: 2,
          },
          1024: {
            slidesPerView: 3,
          },
          1280: {
            slidesPerView: 3,
          },
        },
      });
  
    }); // End of a document
  
    var initPreloader = function () {
      $(document).ready(function ($) {
        var Body = $('body');
        Body.addClass('preloader-site');
      });
      $(window).load(function () {
        $('.preloader-wrapper').fadeOut();
        $('body').removeClass('preloader-site');
      });
    }
  



      // product single page
      var thumb_slider = new Swiper(".product-thumbnail-slider", {
        loop: true,
        slidesPerView: 3,
        autoplay: true,
        direction: "vertical",
        spaceBetween: 15,
      });
  
      var large_slider = new Swiper(".product-large-slider", {
        loop: true,
        slidesPerView: 1,
        autoplay: true,
        effect: 'fade',
        thumbs: {
          swiper: thumb_slider,
        },
      });
  
      window.addEventListener("load", (event) => {
  
        var $grid = $('.entry-container').isotope({
          itemSelector: '.entry-item',
          layoutMode: 'masonry'
        });
  
      });
  
  
  
      initPreloader();
      initProductQty();
  
  
  
  

  
  })(jQuery);